"""GET /api/v1/protocol: how to take an offer, for a program.

Everything here is read from the code that runs the swaps, and each
section names its source:

  EM   Electrum-Mars            github.com/marscoin/electrum-mars
       electrum_mars/atomic_swap_htlc.py
       electrum_mars/btc_monitor.py
       electrum_mars/plugins/atomic_swap/{safety,orderbook,swap_engine,swap_worker,automaker,qt}.py
  MW   Martian Republic wallet  blue_modules/swap/{engine,htlc,safety,mars-chain,esplora}.js
  EX   Marscoin ElectrumX       electrumx/server/{session,atomicswap}.py
  SD   this daemon              atomicswapd/{policy,maker,electrumx,config}.py

Numbers are imported from the installed code where it defines them, so
the document follows the engine this node runs.
"""

from . import policy
from .agentapi import MARS_SPEND_FEE_ZUBRINS, envelope

# What the maker's engine does. Imported when Electrum-Mars is installed
# (always, on a running maker); the fallbacks are the values of 4.4.4.
try:
    from electrum_mars.plugins.atomic_swap import safety as _safety
    MARS_MIN_CONFIRMATIONS = _safety.MARS_MIN_CONFIRMATIONS
    MAKER_CLAIM_MARGIN_BLOCKS = _safety.MAKER_CLAIM_MARGIN_BLOCKS
except ImportError:                                   # pragma: no cover
    MARS_MIN_CONFIRMATIONS = 2
    MAKER_CLAIM_MARGIN_BLOCKS = 3
try:
    from electrum_mars.version import ELECTRUM_VERSION as ENGINE_VERSION
except ImportError:                                   # pragma: no cover
    ENGINE_VERSION = None

MAKER_BTC_CONFIRMATIONS = 1      # EM swap_worker.py _advance_maker: check_htlc_funded(min_confirmations=1)
MAKER_POLL_SECONDS = 30          # EM swap_worker.py POLL_INTERVAL_SEC
BTC_SPEND_VBYTES_WALLET = 150    # MW safety.js BTC_SPEND_VBYTES
BTC_SPEND_VBYTES_ENGINE = 200    # EM swap_engine.py claim_btc / refund_btc_htlc: fee_rate * 200
DUST_SATS = 546                  # MW safety.js DUST
ELECTRUM_PROTOCOL = '1.4'        # SD electrumx.py PROTOCOL; EM version.py PROTOCOL_VERSION

HTLC_ASM = ('OP_IF OP_HASH160 <payment_hash160> OP_EQUALVERIFY <recipient_pubkey> OP_CHECKSIG '
            'OP_ELSE <locktime> OP_CHECKLOCKTIMEVERIFY OP_DROP <sender_pubkey> OP_CHECKSIG OP_ENDIF')
HTLC_HEX = '63 a9 14 <20 bytes> 88 21 <33 bytes> ac 67 <push of locktime> b1 75 21 <33 bytes> ac 68'


def protocol_view(st: dict, now: float) -> dict:
    mc = st.get('maker_config') or {}
    btc_lock, mars_lock = policy.BTC_TIMELOCK_BLOCKS, policy.MARS_MAKER_TIMELOCK_BLOCKS
    return {
        **envelope(now),
        'summary': (
            'Bitcoin for Marscoin atomic swap with one hash time-locked contract (HTLC) on each '
            'chain. The maker sells MARS and locks it first; the taker pays BTC second and can '
            'refund first. This server publishes the market and takes no part in a swap other '
            'than as one of the makers: a taker talks to the relays and to both chains with its '
            'own keys.'),
        'roles': {
            'maker': 'Sells MARS for BTC. Creates the secret, posts the offer, locks MARS first.',
            'taker': 'Buys MARS with BTC. Accepts an offer, verifies the MARS lock, then pays BTC.',
        },
        'units': units(),
        'engine': {
            'electrum_mars_version': ENGINE_VERSION,
            'note': 'The swap engine this maker runs. Wallets may run a newer release.',
        },
        'transport': {
            'description': ('Offers and acceptances travel over the Electrum protocol: JSON-RPC '
                            '2.0, one JSON object per line, over TLS, to Marscoin ElectrumX '
                            'servers that carry the atomicswap.* extension. There is no HTTP '
                            'endpoint for accepting an offer, here or anywhere.'),
            'relays': [{'host': r['host'], 'port': r['port'],
                        'tls_certificate_verifiable': r['tls_verify']}
                       for r in st.get('relays', [])],
            'relays_note': ('The relays this node watches. A relay whose certificate cannot be '
                            'verified is self-signed: prefer one that can.'),
            'handshake': {'method': 'server.version',
                          'params': ['<your client name>', ELECTRUM_PROTOCOL]},
            'request_example': {'jsonrpc': '2.0', 'id': 1, 'method': 'atomicswap.get_offers',
                                'params': []},
            'source': 'SD electrumx.py; MW mars-chain.js SWAP_RELAY',
        },
        'relay_methods': [
            {'method': 'atomicswap.get_offers', 'params': [], 'used_by': 'taker',
             'result': 'array of offer objects (see offer_format)',
             'source': 'EX session.py atomicswap_get_offers'},
            {'method': 'atomicswap.accept_offer', 'params': ['offer_id', 'acceptance'],
             'used_by': 'taker',
             'result': 'true, or a JSON-RPC error whose message says why (see relay_limits)',
             'source': 'EX session.py atomicswap_accept_offer; EM qt.py _accept_offer; '
                       'MW engine.js announce'},
            {'method': 'atomicswap.get_acceptance', 'params': ['offer_id'], 'used_by': 'maker',
             'result': 'the acceptance plus taker_tag and accepted_at, or null. Anyone can read it.',
             'source': 'EX atomicswap.py get_acceptance; EM swap_worker.py _check_for_acceptance'},
            {'method': 'atomicswap.post_offer', 'params': ['offer'], 'used_by': 'maker',
             'result': 'true', 'source': 'EX session.py atomicswap_post_offer; EM orderbook.py'},
            {'method': 'atomicswap.cancel_offer', 'params': ['offer_id'], 'used_by': 'maker',
             'result': 'true or false; only the poster may cancel',
             'source': 'EX atomicswap.py cancel_offer'},
            {'method': 'atomicswap.report_abandoned', 'params': ['offer_id'], 'used_by': 'maker',
             'result': 'true or false; blocks the taker from accepting for a while',
             'source': 'EX atomicswap.py report_abandoned; EM swap_worker.py _report_abandoned'},
            {'method': 'atomicswap.features', 'params': [], 'used_by': 'anyone',
             'result': '{abuse_controls, watchtower, watchtower_version}',
             'source': 'EX session.py atomicswap_capabilities'},
            {'method': 'blockchain.headers.subscribe', 'params': [], 'used_by': 'taker',
             'result': '{height, hex}: the Marscoin tip', 'source': 'Electrum protocol'},
            {'method': 'blockchain.scripthash.listunspent', 'params': ['scripthash'],
             'used_by': 'taker',
             'result': 'array of {tx_hash, tx_pos, value, height}. scripthash is the SHA-256 of '
                       'the output script, byte-reversed, in hex.',
             'source': 'EM swap_engine.py _mars_lockbox_outputs; MW htlc.js electrumScriptHash'},
            {'method': 'blockchain.transaction.get', 'params': ['txid'], 'used_by': 'taker',
             'result': 'raw transaction hex', 'source': 'Electrum protocol'},
            {'method': 'blockchain.transaction.get_merkle', 'params': ['txid', 'height'],
             'used_by': 'taker', 'result': '{merkle: [hex...], pos, block_height}',
             'source': 'EM swap_engine.py _spv_verified; MW engine.js verifyMarsLock'},
            {'method': 'blockchain.block.header', 'params': ['height'], 'used_by': 'taker',
             'result': 'block header hex (merkle root at bytes 36..68)',
             'source': 'MW htlc.js headerMerkleRoot'},
            {'method': 'blockchain.transaction.broadcast', 'params': ['raw_tx_hex'],
             'used_by': 'taker', 'result': 'txid', 'source': 'Electrum protocol'},
        ],
        'offer_format': {
            'fields': {
                'offer_id': 'string of hex characters (32 from Electrum-Mars makers)',
                'mars_amount_sat': 'integer, zubrins of MARS sold',
                'btc_amount_sat': 'integer, satoshis of BTC wanted',
                'rate': 'number, BTC per MARS. Informational: recompute from the two amounts.',
                'maker_pubkey': 'string, 33-byte compressed public key in hex, one per offer',
                'payment_hash160': 'string, 20 bytes in hex: RIPEMD160(SHA256(secret))',
                'mars_locktime': 'integer, Marscoin block height at which the maker can refund',
                'expires_at': 'number, unix seconds',
                'mars_htlc_address': 'string, empty in a posted offer. Never use it.',
                'mars_htlc_script': 'string, empty in a posted offer. Never use it.',
                'maker_address': 'string, a Marscoin address of the maker. Informational.',
                'last_seen': 'number, unix seconds',
                'signature': 'string, empty: offers are not signed',
            },
            'trust': ('Offers are unsigned bulletin-board entries. Use only offer_id, the two '
                      'amounts, maker_pubkey, payment_hash160, mars_locktime and expires_at, and '
                      'only after checking their form. Build both contracts yourself.'),
            'source': 'EM orderbook.py SwapOffer, automaker.py create_offers; MW safety.js normalizeOffer',
        },
        'acceptance_format': {
            'fields': {
                'taker_pubkey': 'string, 33-byte compressed public key in hex; a fresh key per swap',
                'btc_htlc_address': 'string, the bc1 P2WSH address of the Bitcoin contract you built',
                'btc_locktime': f'integer, Bitcoin block height: the tip when you accept plus {btc_lock}',
                'timestamp': 'number, unix seconds',
            },
            'example': {'taker_pubkey': '02' + 'ab' * 32,
                        'btc_htlc_address': 'bc1q...(62 characters)', 'btc_locktime': 900024,
                        'timestamp': 1794000000},
            'maker_checks': [
                'btc_locktime is a positive integer',
                f'at least {policy.MIN_BTC_WINDOW_SEC} seconds of Bitcoin blocks remain before btc_locktime',
                'the refund order holds (see taker_checks.refund_order) with this btc_locktime',
                'btc_htlc_address must be the address computed from payment_hash160, maker_pubkey '
                '(recipient), taker_pubkey (sender) and btc_locktime: the maker watches and '
                'claims only that address',
                f'the taker has fewer than {mc.get("max_pending_per_taker", 1)} other swap(s) '
                'in progress with this maker',
            ],
            'source': 'EM qt.py _accept_offer, swap_worker.py _advance_maker and _acceptance_problem; '
                      'SD maker.py GuardedSwapWorker',
        },
        'relay_limits': {
            'first_accept_wins': 'A second taker gets "offer already taken".',
            'open_acceptances_per_client': 1,
            'accepts_per_client_per_day': 6,
            'block_hours_after_reported_abandonment': 24,
            'client_identity': 'a keyed hash of the IP address (IPv6 by /64)',
            'refusal_messages': ['unknown or expired offer', 'offer already taken',
                                 'you already have a swap in progress; finish it before accepting another',
                                 'daily limit for accepting offers reached',
                                 'too many abandoned swaps from this connection; try again later'],
            'unconfirmed': ('The numbers are the defaults in the relay source. Each relay can '
                            'change them through its environment; the live values were not read.'),
            'source': 'EX atomicswap.py SwapRelay',
        },
        'htlc': {
            'script_asm': HTLC_ASM,
            'script_hex_layout': HTLC_HEX,
            'payment_hash160': 'RIPEMD160(SHA256(secret)); the secret is 32 random bytes made by the maker',
            'pubkeys': '33-byte compressed secp256k1 keys',
            'locktime': ('absolute block height below 500000000, pushed as a minimally encoded '
                         'script number (little endian, a 0x00 byte appended if the top bit is set)'),
            'address': 'native P2WSH: witness version 0, program SHA256(script), bech32',
            'marscoin_contract': {'recipient_pubkey': 'taker', 'sender_pubkey': 'maker',
                                  'locktime': 'offer.mars_locktime', 'bech32_hrp': 'mars'},
            'bitcoin_contract': {'recipient_pubkey': 'maker', 'sender_pubkey': 'taker',
                                 'locktime': f'Bitcoin tip at acceptance + {btc_lock}',
                                 'bech32_hrp': 'bc'},
            'claim': {'witness': ['<signature>', '<secret>', '01', '<script>'],
                      'by': 'the recipient, at any time, with the secret'},
            'refund': {'witness': ['<signature>', '<empty>', '<script>'],
                       'by': 'the sender, once the chain reaches the locktime',
                       'nlocktime': 'the contract locktime', 'nsequence': '0xfffffffe'},
            'spend_format': 'version 2, one input, one output, SIGHASH_ALL, BIP143 digest over '
                            'the script and the exact value of the output being spent',
            'source': 'EM atomic_swap_htlc.py create_htlc_script, htlc_to_p2wsh_address, '
                      'create_claim_tx, create_refund_tx; MW htlc.js; EM safety.py expected_mars_htlc; '
                      'MW engine.js swapContracts',
        },
        'timelocks': {
            'bitcoin_refund_blocks': btc_lock,
            'marscoin_refund_blocks_this_maker': mars_lock,
            'marscoin_refund_blocks_older_makers': policy.MARS_TIMELOCK_BLOCKS,
            'bitcoin_target_block_seconds': policy.BTC_BLOCK_SEC,
            'marscoin_target_block_seconds': policy.MARS_BLOCK_SEC,
            'note': ('mars_locktime is fixed when the offer is created (the maker\'s Marscoin '
                     f'height then, plus {mars_lock}) and does not move when a taker accepts, so '
                     'the blocks left shrink while an offer waits. Current wallets refuse the '
                     f'{policy.MARS_TIMELOCK_BLOCKS}-block lock of older makers.'),
            'source': 'EM atomic_swap_htlc.py BTC_TIMELOCK_BLOCKS, MARS_MAKER_TIMELOCK_BLOCKS; '
                      'swap_engine.py create_maker_swap, create_taker_swap; SD policy.py',
        },
        'confirmations': {
            'marscoin_lock_before_taker_pays': MARS_MIN_CONFIRMATIONS,
            'bitcoin_payment_before_maker_claims': MAKER_BTC_CONFIRMATIONS,
            'maker_claim_cutoff_blocks_before_bitcoin_refund': MAKER_CLAIM_MARGIN_BLOCKS,
            'source': 'EM safety.py MARS_MIN_CONFIRMATIONS, MAKER_CLAIM_MARGIN_BLOCKS; '
                      'swap_worker.py _advance_maker',
        },
        'sequence': [
            {'step': 1, 'actor': 'taker', 'action': 'Read offers',
             'detail': 'atomicswap.get_offers on a relay (or GET /api/v1/offers here, then check '
                       'the offer exists on a relay). Validate each field.'},
            {'step': 2, 'actor': 'taker', 'action': 'Check the clocks and the fee',
             'detail': 'Apply taker_checks.refund_order, bitcoin_window and fee_viability with '
                       'block heights from sources you trust.'},
            {'step': 3, 'actor': 'taker', 'action': 'Create a key and both contracts',
             'detail': 'A fresh private key. Build the Marscoin and the Bitcoin script and '
                       'address. Store the key, the offer values and btc_locktime durably before '
                       'the next step: they are what a refund needs.'},
            {'step': 4, 'actor': 'taker', 'action': 'Accept',
             'detail': 'atomicswap.accept_offer [offer_id, acceptance]. Nothing is paid.'},
            {'step': 5, 'actor': 'maker', 'action': 'Lock MARS',
             'detail': f'The maker polls for acceptances about every {MAKER_POLL_SECONDS} seconds, '
                       'checks the acceptance and pays the MARS amount to the Marscoin contract.'},
            {'step': 6, 'actor': 'taker', 'action': 'Verify the MARS lock',
             'detail': 'All of taker_checks.mars_lock. Until it passes, send nothing.'},
            {'step': 7, 'actor': 'taker', 'action': 'Pay BTC',
             'detail': 'Repeat the clock and fee checks, then pay exactly btc_amount_sat to the '
                       'Bitcoin contract address in one output.'},
            {'step': 8, 'actor': 'maker', 'action': 'Claim BTC',
             'detail': f'After {MAKER_BTC_CONFIRMATIONS} confirmation(s), and only while at least '
                       f'{MAKER_CLAIM_MARGIN_BLOCKS} blocks remain before btc_locktime. The claim '
                       'puts the secret on the Bitcoin chain.'},
            {'step': 9, 'actor': 'taker', 'action': 'Read the secret and claim MARS',
             'detail': 'Find the transaction spending the Bitcoin contract; the secret is the '
                       '32-byte witness item whose HASH160 is payment_hash160. Claim the Marscoin '
                       'contract with it before mars_locktime.'},
        ],
        'taker_checks': {
            'offer_form': {
                'rule': ('offer_id 8 to 64 hex characters; maker_pubkey 02/03 plus 64 hex; '
                         'payment_hash160 40 hex; integer amounts above dust; mars_locktime a '
                         'height below 500000000; expires_at in the future'),
                'source': 'MW safety.js normalizeOffer'},
            'refund_order': {
                'rule': 'The Marscoin refund must open at least min_gap_seconds after the '
                        'Bitcoin refund, assuming fast Marscoin blocks and slow Bitcoin blocks.',
                'formula': '(mars_locktime - mars_height) * marscoin_block_seconds * mars_fast '
                           '- (btc_locktime - btc_height) * bitcoin_block_seconds * btc_slow '
                           '>= min_gap_seconds',
                'marscoin_block_seconds': policy.MARS_BLOCK_SEC,
                'bitcoin_block_seconds': policy.BTC_BLOCK_SEC,
                'mars_fast': policy.MARS_FAST,
                'btc_slow': policy.BTC_SLOW,
                'min_gap_seconds': policy.MIN_REFUND_GAP_SEC,
                'when': 'before accepting (with btc_locktime = btc_height + '
                        f'{btc_lock}) and again before paying (with the agreed btc_locktime)',
                'source': 'EM safety.py refund_order_problem; MW safety.js refundOrderProblem; '
                          'SD policy.py offer_safety'},
            'bitcoin_window': {
                'rule': 'Do not pay unless enough Bitcoin blocks remain before your own refund.',
                'formula': '(btc_locktime - btc_height) * bitcoin_block_seconds >= min_window_seconds',
                'min_window_seconds': policy.MIN_BTC_WINDOW_SEC,
                'source': 'EM safety.py btc_window_problem; MW safety.js btcWindowProblem'},
            'fee_viability': {
                'rule': 'The Bitcoin you lock must be worth refunding at the current fee rate.',
                'formula': 'btc_amount_sat - ceil(fee_rate_sat_per_vb * spend_vbytes) >= dust_sats',
                'spend_vbytes': BTC_SPEND_VBYTES_WALLET,
                'dust_sats': DUST_SATS,
                'note': 'The phone wallet applies this. Electrum-Mars does not check it; its own '
                        f'refund pays fee rate times {BTC_SPEND_VBYTES_ENGINE} vbytes.',
                'source': 'MW safety.js btcFeeProblem; EM swap_engine.py refund_btc_htlc'},
            'block_heights': {
                'rule': 'Take heights from more than one source. A stale Marscoin height makes '
                        'an expiring lock look safe; an inflated Bitcoin height delays your refund.',
                'phone_wallet': 'Marscoin: relay tip and explore.marscoin.org/api/status must '
                                'agree within 3 blocks, the higher is used. Bitcoin: mempool.space '
                                'and blockstream.info must agree within 2 blocks, the lower is used.',
                'electrum_mars': 'Marscoin: its own verified header chain. Bitcoin: mempool.space.',
                'source': 'MW mars-chain.js agreedTipHeight, esplora.js agreedTipHeight; '
                          'EM swap_engine.py verify_taker_mars_lock'},
            'mars_lock': {
                'rules': [
                    'Build the expected Marscoin script and address yourself from payment_hash160, '
                    'your pubkey, maker_pubkey and mars_locktime. Ignore any address the relay gives.',
                    'Find an unspent output paying exactly that script with at least mars_amount_sat.',
                    f'It has at least {MARS_MIN_CONFIRMATIONS} confirmations '
                    '(tip height - output height + 1).',
                    'Fetch the raw transaction, check it hashes to the txid and that the output '
                    'pays the script and the amount.',
                    'Check the Merkle proof of the transaction against a block header you have '
                    'verified, not against the server\'s word.',
                    'The phone wallet also requires a second full node (explore.marscoin.org) to '
                    'show the same output, and rechecks that the output is still unspent just '
                    'before paying.',
                ],
                'source': 'EM swap_engine.py verify_taker_mars_lock, _spv_verified; safety.py '
                          'pick_mars_funding; MW engine.js verifyMarsLock, pay'},
        },
        'fees': {
            'marscoin_claim_or_refund_zubrins': MARS_SPEND_FEE_ZUBRINS,
            'marscoin_fee_note': 'Fixed, and taken from the contract amount: a taker receives '
                                 'mars_amount_sat minus this.',
            'bitcoin_payment': 'The taker\'s own transaction fee, on top of btc_amount_sat.',
            'bitcoin_claim': f'Paid by the maker out of the contract: mempool.space halfHourFee '
                             f'times {BTC_SPEND_VBYTES_ENGINE} vbytes.',
            'source': 'EM atomic_swap_htlc.py create_claim_tx, create_refund_tx; '
                      'swap_engine.py claim_btc; btc_monitor.py get_fee_rate; MW safety.js MARS_SPEND_FEE',
        },
        'this_maker': {
            'offer_lifetime_seconds': int(mc.get('offer_minutes', 15) * 60),
            'offer_refresh_seconds': mc.get('refresh_seconds'),
            'max_swaps_in_progress_per_taker': mc.get('max_pending_per_taker'),
            'min_offer_mars': mc.get('min_offer_mars'),
            'max_offer_mars': mc.get('max_offer_mars'),
            'acceptance_poll_seconds': MAKER_POLL_SECONDS,
            'note': 'Offers are replaced at each refresh with new ids and prices. Fetch an '
                    'offer again just before accepting it.',
            'source': 'SD config.py Maker, maker.py; EM swap_worker.py POLL_INTERVAL_SEC',
        },
        'recovery': [
            {'situation': 'Accepted, MARS never locked or never verified',
             'at_risk': 'nothing',
             'action': 'Do not pay. The swap ends. A maker that locked MARS and saw no BTC may '
                       'report the acceptance, and the relay then refuses your acceptances for a while.'},
            {'situation': 'Paid BTC, the maker never claims',
             'at_risk': 'time only',
             'action': 'Once the Bitcoin chain reaches btc_locktime, spend the contract back to '
                       'yourself with the refund witness. You need the private key, payment_hash160, '
                       'maker_pubkey and btc_locktime.'},
            {'situation': 'The maker claimed the BTC',
             'at_risk': 'the MARS, if you do not claim it before mars_locktime',
             'action': 'Read the secret from the claim transaction and claim the Marscoin contract '
                       'at once. After mars_locktime the maker can take the MARS back, and you '
                       'would have paid for nothing. A taker must stay online, or hand the claim '
                       'to a watchtower, from payment until the claim confirms.'},
            {'situation': 'You paid less than, more than, or twice the agreed amount',
             'at_risk': 'the payment until the refund',
             'action': 'Pay exactly btc_amount_sat in one output. A smaller output is ignored, '
                       'and the Electrum-Mars maker signs its claim for the agreed amount, so a '
                       'larger one cannot be claimed either. Such a payment stays in the contract '
                       'until you refund it after btc_locktime.'},
        ],
        'watchtower': {
            'methods': ['atomicswap.watch_register', 'atomicswap.watch_status'],
            'unconfirmed': 'The phone wallet hands pre-signed claim and refund transactions to a '
                           'relay watchtower. The package format is in the ElectrumX repository '
                           '(electrumx/server/watchtower.py) and is not described here.',
        },
        'what_this_server_does': [
            'Reads the order book from the relays and publishes it, with prices and a safety verdict.',
            'Runs one maker, which posts offers and completes swaps with its own wallet.',
        ],
        'what_this_server_does_not_do': [
            'It has no endpoint that accepts an offer, takes a payment, or signs anything.',
            'It never holds a taker\'s keys or coins, and cannot move them.',
            'It cannot cancel, reverse or speed up a swap.',
            'Its safety verdict is a convenience, computed from its own view of both chains. '
            'Recompute it.',
        ],
        'unconfirmed': [
            'relay_limits: defaults from the relay source, not read from the live relays.',
            'watchtower: package format not described.',
            'blockchain.scripthash.repair: a Marscoin ElectrumX extension that Electrum-Mars '
            'falls back to when listunspent is empty; its availability on each relay was not checked.',
        ],
    }


def units() -> dict:
    return {
        'MARS': 'Marscoin. Decimal amounts, 8 places.',
        'zubrin': '1e-8 MARS, the smallest unit. Fields ending _zubrins are integers. The relay '
                  'protocol calls the same unit mars_amount_sat.',
        'BTC': 'Bitcoin. Decimal amounts, 8 places.',
        'satoshi': '1e-8 BTC. Fields ending _sats are integers.',
        'price': 'BTC per MARS (equal to satoshis per zubrin); also satoshis per whole MARS.',
        'time': 'Fields ending _at are ISO 8601 UTC; _at_unix and _unix are unix seconds; '
                '_seconds are durations.',
        'height': 'Block heights are absolute; _blocks are counts.',
    }
