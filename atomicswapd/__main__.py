"""atomicswapd command line.

  atomicswapd                  run the maker and the dashboard
  atomicswapd wallet create    create the hot wallet (prints the seed once)
  atomicswapd wallet address   show the deposit address
  atomicswapd check            validate the configuration and exit
"""

import argparse
import asyncio
import logging
import signal
import sys

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

    future = asyncio.run_coroutine_threadsafe(main(), node.loop)

    def on_term(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, on_term)
    try:
        node.run_until_stopped()
    finally:
        node.loop.call_soon_threadsafe(service.stop.set)
        try:
            future.result(timeout=10)
        except Exception:
            pass


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


def _wallet(cfg, action):
    from .node import create_wallet, wallet_password
    if action == 'create':
        result = create_wallet(cfg, wallet_password())
        print('\nWrite this seed down and keep it offline. It is shown only once.\n')
        print('   ' + result['seed'] + '\n')
        print(f'Deposit address: {result["address"]}\n')
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
    sub.add_parser('check', help='validate the configuration and exit')
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    try:
        cfg = load(args.config)
        if cfg.mode == 'live':
            from .maker import check_btc_address
            check_btc_address(cfg.maker.btc_receive_address)
    except ConfigError as e:
        sys.exit(f'configuration error: {e}')
    if args.command == 'wallet':
        _wallet(cfg, args.action)
    elif args.command == 'check':
        print(f'ok: mode={cfg.mode}, {len(cfg.servers)} relays, data in {cfg.data_dir}')
    elif args.no_node:
        _run_without_node(cfg)
    else:
        _run(cfg)


if __name__ == '__main__':
    main()
