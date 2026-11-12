"""A Model Context Protocol server over stdio, for the read tools.

Standard library only: JSON-RPC 2.0, one message per line on stdin and
stdout. The tools are the node's own (/api/tools), fetched when asked, plus
one local tool, check_offer, which recomputes an offer's safety and
compares it with the server. Every tool reads; none writes, signs or pays.

  python -m marsswap_agent mcp [--url https://swaps.marscoin.org]
"""

import json
import sys
import urllib.parse

from . import __version__, report
from .client import ApiError, NetworkError, http_get

PROTOCOL_VERSION = '2025-06-18'
SUPPORTED_VERSIONS = ('2025-06-18', '2025-03-26', '2024-11-05')

CHECK_OFFER = {
    'name': 'check_offer',
    'description': (
        'Recompute, locally, whether an open offer is safe to accept, and compare the result '
        'with the server\'s own safety verdict. Checks the form of the offer, that it is open, '
        'the refund order of the two timelocks, and that the Bitcoin amount is worth refunding '
        'at current fees. The Marscoin block height must also agree with a public block '
        'explorer, unless independent=false. With taker_pubkey (a PUBLIC key, never a private one) it '
        'returns the contract addresses for that key. A safe result allows accepting an offer; '
        'it does not allow paying Bitcoin, which needs the Marscoin lock verified on chain. '
        'Read-only: nothing is accepted, signed or sent.'),
    'inputSchema': {
        'type': 'object',
        'properties': {
            'id': {'type': 'string', 'description': 'Offer id, or a unique prefix of 8 or more '
                                                    'hex characters.'},
            'independent': {'type': 'boolean', 'description': 'Check block heights against '
                                                              'public explorers. Default true.'},
            'taker_pubkey': {'type': 'string', 'description': 'Your 33-byte compressed public '
                                                              'key in hex. Optional.'},
        },
        'required': ['id'], 'additionalProperties': False,
    },
}


class Server:

    def __init__(self, client, fetch=http_get):
        self.client = client
        self.fetch = fetch
        self._remote = None

    def remote(self) -> dict:
        if self._remote is None:
            self._remote = self.client.tools()
        return self._remote

    def list_tools(self) -> list:
        tools = [{'name': t['name'], 'description': t['description'],
                  'inputSchema': t['input_schema']} for t in self.remote()['tools']]
        return tools + [CHECK_OFFER]

    def call_tool(self, name: str, arguments: dict):
        if name == 'check_offer':
            if not isinstance(arguments.get('id'), str):
                raise ValueError('id is required')
            return report.check_offer(self.client, arguments['id'],
                                      independent=arguments.get('independent', True) is not False,
                                      taker_pubkey=arguments.get('taker_pubkey'),
                                      fetch=self.fetch)
        how = self.remote()['execution'].get(name)
        if how is None:
            raise ValueError(f'unknown tool: {name}')
        allowed = set(how['path_parameters']) | set(how['query_parameters'])
        unknown = set(arguments) - allowed
        if unknown:
            raise ValueError(f'unknown argument(s): {", ".join(sorted(unknown))}')
        path = urllib.parse.urlsplit(how['url']).path
        for key in how['path_parameters']:
            if key not in arguments:
                raise ValueError(f'{key} is required')
            path = path.replace('{%s}' % key, urllib.parse.quote(str(arguments[key]), safe=''))
        query = {k: arguments[k] for k in how['query_parameters'] if k in arguments}
        # Always the node this server was started for, whatever url the document names.
        return self.client.get(path, **query)

    def handle(self, message: dict):
        """One JSON-RPC message in; a response dict, or None for a notification."""
        msg_id, method = message.get('id'), message.get('method')
        params = message.get('params') or {}
        if msg_id is None:
            return None                                  # a notification: nothing to answer

        def result(value):
            return {'jsonrpc': '2.0', 'id': msg_id, 'result': value}

        def error(code, text):
            return {'jsonrpc': '2.0', 'id': msg_id, 'error': {'code': code, 'message': text}}

        if method == 'initialize':
            wanted = params.get('protocolVersion')
            return result({
                'protocolVersion': wanted if wanted in SUPPORTED_VERSIONS else PROTOCOL_VERSION,
                'capabilities': {'tools': {}},
                'serverInfo': {'name': 'marsswap-agent', 'version': __version__},
                'instructions': ('Read-only tools for the Bitcoin to Marscoin atomic swap market. '
                                 'Offers are taken whole. Nothing here accepts an offer or moves '
                                 'coins; call get_protocol to learn how a swap is carried out.'),
            })
        if method == 'ping':
            return result({})
        try:
            if method == 'tools/list':
                return result({'tools': self.list_tools()})
            if method == 'tools/call':
                name, arguments = params.get('name'), params.get('arguments') or {}
                if not isinstance(name, str) or not isinstance(arguments, dict):
                    return error(-32602, 'tools/call needs a name and an arguments object')
                try:
                    data = self.call_tool(name, arguments)
                except ApiError as e:
                    return result(_text({'error': {'code': e.code, 'message': e.message}}, True))
                except ValueError as e:
                    return result(_text({'error': {'code': 'invalid_arguments',
                                                   'message': str(e)}}, True))
                return result(_text(data, False))
        except NetworkError as e:
            return error(-32000, f'the swap node could not be reached: {e}')
        return error(-32601, f'method not found: {method}')


def _text(data, is_error: bool) -> dict:
    return {'content': [{'type': 'text', 'text': json.dumps(data, indent=2)}], 'isError': is_error}


def serve(client, fetch=http_get, stdin=None, stdout=None) -> int:
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    server = Server(client, fetch)
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
            if not isinstance(message, dict):
                raise ValueError
        except ValueError:
            response = {'jsonrpc': '2.0', 'id': None,
                        'error': {'code': -32700, 'message': 'parse error'}}
        else:
            response = server.handle(message)
        if response is not None:
            stdout.write(json.dumps(response, separators=(',', ':')) + '\n')
            stdout.flush()
    return 0
