"""atomicswapd command line.

  atomicswapd                  run the maker and the dashboard
  atomicswapd status [URL]     setup stage and next step of a running maker
  atomicswapd wallet create    create the hot wallet (prints the seed once,
                               or --seed-file PATH writes it to a private file)
  atomicswapd wallet address   show the deposit address
  atomicswapd check            validate the configuration and exit
"""

import argparse
import asyncio
import logging
import os
import signal
import sys
import threading

from . import __version__
from .config import ConfigError, load


def _run(cfg):
    from .api import serve
    from .node import Node
    from .service import Service

    node = Node(cfg).start()
    service = Service(cfg, node)

    async def main():
        runner = await serve(service, cfg.api_host, cfg.api_port, cfg.max_stream_clients)
        try:
            await service.run()
        finally:
            await runner.cleanup()

    # The dashboard gets its own loop and thread; Electrum-Mars keeps its own.
    dash_loop = asyncio.new_event_loop()
    threading.Thread(target=dash_loop.run_forever, name='dashboard', daemon=True).start()
    future = asyncio.run_coroutine_threadsafe(main(), dash_loop)

    def on_term(signum, frame):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)   # one is enough
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, on_term)
    try:
        node.run_until_stopped()   # returns once wallets and network are stopped and saved
    finally:
        dash_loop.call_soon_threadsafe(service.stop.set)
        try:
            future.result(timeout=10)
        except Exception:
            pass
        logging.getLogger(__name__).info('stopped')
        logging.shutdown()
        # Electrum leaves helper threads behind; everything is saved, so
        # don't wait for them (that wait is what the platform kills).
        os._exit(0)


def _run_without_node(cfg):
    """Development: the dashboard on real relay and market data, without
    the Electrum-Mars node or a wallet."""
    from .api import serve
    from .service import Service

    async def main():
        service = Service(cfg, node=None)
        runner = await serve(service, cfg.api_host, cfg.api_port, cfg.max_stream_clients)
        try:
            await service.run()
        finally:
            await runner.cleanup()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass


def _wallet(cfg, action, seed_file=None):
    from .node import create_wallet, wallet_password
    if action == 'create':
        result = create_wallet(cfg, wallet_password())
        if seed_file:
            # For automated setups: the seed never appears in a terminal or log.
            fd = os.open(seed_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as f:
                f.write(result['seed'] + '\n')
            _match_owner(seed_file, cfg.data_dir)
            print(f'Seed written to {seed_file} (readable only by its owner).')
            print('The operator must copy it somewhere offline and then delete that file.')
        else:
            print('\nWrite this seed down and keep it offline. It is shown only once.\n')
            print('   ' + result['seed'] + '\n')
        print(f'Deposit address: {result["address"]}')
        print('Restart the daemon to load the wallet.')
    elif action == 'address':
        from electrum_mars import util
        from electrum_mars.storage import WalletStorage
        from electrum_mars.wallet import Wallet
        from electrum_mars.wallet_db import WalletDB
        from .node import electrum_config
        util.create_and_start_event_loop()
        storage = WalletStorage(cfg.wallet_path)
        if not storage.file_exists():
            raise SystemExit('no wallet yet; run: atomicswapd wallet create')
        if storage.is_encrypted():
            storage.decrypt(wallet_password())
        db = WalletDB(storage.read(), manual_upgrades=False)
        wallet = Wallet(db, storage, config=electrum_config(cfg))
        print(wallet.get_receiving_address())


def _match_owner(path, like):
    if hasattr(os, 'geteuid') and os.geteuid() == 0:
        st = os.stat(like)
        os.chown(path, st.st_uid, st.st_gid)


def _status(url, as_json):
    """Print a running maker's setup stage and next step (exit 0 when live)."""
    import json
    import urllib.request
    base = url.rstrip('/') if url else 'http://localhost:8080'
    with urllib.request.urlopen(base + '/api/snapshot', timeout=15) as resp:
        snap = json.load(resp)
    setup, maker = snap.get('setup') or {}, snap.get('maker') or {}
    node, bal = maker.get('node') or {}, maker.get('balance') or {}
    summary = {
        'stage': setup.get('stage'), 'next': setup.get('next'), 'mode': snap.get('mode'),
        'headers': [node.get('local_height'), node.get('server_height')],
        'inventory_mars': bal.get('inventory'), 'deposit_address': bal.get('deposit_address'),
        'offers_posted': len(maker.get('offers') or []), 'would_post': maker.get('plan'),
        'swaps': len(maker.get('swaps') or []), 'dashboard': base,
    }
    if as_json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"stage:    {summary['stage']}  (mode {summary['mode']})")
        print(f"next:     {summary['next']}")
        print(f"headers:  {summary['headers'][0]} / {summary['headers'][1]}")
        if summary['deposit_address']:
            print(f"wallet:   {summary['inventory_mars']} MARS at {summary['deposit_address']}")
        print(f"offers:   {summary['offers_posted']} posted, {len(summary['would_post'] or [])} planned")
    return 0 if summary['stage'] == 'live' else 2


def main(argv=None):
    parser = argparse.ArgumentParser(prog='atomicswapd', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('-c', '--config', help='TOML config file (or ATOMICSWAPD_CONFIG)')
    parser.add_argument('--version', action='version', version=f'atomicswapd {__version__}')
    parser.add_argument('--no-node', action='store_true',
                        help='development: dashboard only, without the wallet node')
    sub = parser.add_subparsers(dest='command')
    w = sub.add_parser('wallet', help='manage the hot wallet')
    w.add_argument('action', choices=['create', 'address'])
    w.add_argument('--seed-file', help='write the new seed to this file (mode 0600) '
                                       'instead of printing it')
    sub.add_parser('check', help='validate the configuration and exit')
    st = sub.add_parser('status', help="a running maker's setup stage and next step")
    st.add_argument('url', nargs='?', help='dashboard URL (default http://localhost:8080)')
    st.add_argument('--json', action='store_true', help='machine-readable output')
    args = parser.parse_args(argv)
    if args.command == 'status':
        sys.exit(_status(args.url, args.json))

    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    # Electrum-Mars logs every request at INFO; keep it quiet unless asked.
    logging.getLogger('electrum_mars').setLevel(
        os.environ.get('ATOMICSWAPD_ELECTRUM_LOG', 'WARNING').upper() or 'WARNING')
    try:
        cfg = load(args.config)
        if cfg.mode == 'live':
            from .maker import check_btc_address
            check_btc_address(cfg.maker.btc_receive_address)
    except ConfigError as e:
        sys.exit(f'configuration error: {e}')
    if args.command == 'wallet':
        _wallet(cfg, args.action, getattr(args, 'seed_file', None))
    elif args.command == 'check':
        print(f'ok: mode={cfg.mode}, {len(cfg.servers)} relays, data in {cfg.data_dir}')
    elif args.no_node:
        _run_without_node(cfg)
    else:
        _run(cfg)


if __name__ == '__main__':
    main()
