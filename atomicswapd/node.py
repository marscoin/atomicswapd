"""Electrum-Mars without a GUI: the network, header chain and hot wallet."""

import logging
import os
from typing import Optional

from electrum_mars import util
from electrum_mars.daemon import Daemon
from electrum_mars.simple_config import SimpleConfig
from electrum_mars.wallet import Abstract_Wallet, create_new_wallet

from .config import Config

log = logging.getLogger(__name__)

PASSWORD_ENV = 'ATOMICSWAPD_WALLET_PASSWORD'


def wallet_password(env=os.environ) -> Optional[str]:
    return env.get(PASSWORD_ENV) or None


def electrum_config(cfg: Config) -> SimpleConfig:
    os.makedirs(os.path.join(cfg.electrum_dir, 'wallets'), exist_ok=True)
    return SimpleConfig({
        'electrum_path': cfg.electrum_dir,
        'auto_connect': True,
        'log_to_file': False,
    })


def create_wallet(cfg: Config, password: str) -> dict:
    """Create the hot wallet. Returns the seed (shown once) and an address."""
    if not password:
        raise SystemExit(f'set {PASSWORD_ENV} first; the wallet file is encrypted with it')
    if os.path.exists(cfg.wallet_path):
        raise SystemExit(f'a wallet already exists at {cfg.wallet_path}')
    util.create_and_start_event_loop()
    config = electrum_config(cfg)
    result = create_new_wallet(path=cfg.wallet_path, config=config, password=password,
                               encrypt_file=True, seed_type='segwit')
    _match_data_dir_owner(cfg)
    return {'seed': result['seed'], 'address': result['wallet'].get_receiving_address()}


def _match_data_dir_owner(cfg: Config):
    """Run as root (e.g. over `fly ssh`), hand the files to the daemon's user."""
    if not hasattr(os, 'geteuid') or os.geteuid() != 0:
        return
    st = os.stat(cfg.data_dir)
    for root, dirs, files in os.walk(cfg.electrum_dir):
        for name in [root] + [os.path.join(root, f) for f in files]:
            os.chown(name, st.st_uid, st.st_gid)


class Node:
    """The Electrum-Mars daemon, its event loop and (if set up) the wallet."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.loop = None
        self.daemon: Optional[Daemon] = None
        self.wallet: Optional[Abstract_Wallet] = None
        self.wallet_error: Optional[str] = None

    def start(self):
        self.loop, _stopping, _thread = util.create_and_start_event_loop()
        config = electrum_config(self.cfg)
        self.daemon = Daemon(config, listen_jsonrpc=False)
        if not os.path.exists(self.cfg.wallet_path):
            self.wallet_error = 'no wallet yet (run: atomicswapd wallet create)'
            log.warning(self.wallet_error)
            return self
        try:
            self.wallet = self.daemon.load_wallet(
                self.cfg.wallet_path, wallet_password(), manual_upgrades=False)
        except Exception as e:
            self.wallet_error = f'could not open the wallet: {e}'
            log.error(self.wallet_error)
        return self

    @property
    def network(self):
        return self.daemon.network if self.daemon else None

    def run_until_stopped(self):
        """Block the main thread until SIGINT/SIGTERM, then shut down cleanly."""
        self.daemon.run_daemon()

    def status(self) -> dict:
        net = self.network
        wallet = self.wallet
        return {
            'connected': bool(net and net.is_connected()),
            'server': str(net.interface.server) if net and net.interface else None,
            'local_height': net.get_local_height() if net else None,
            'server_height': net.get_server_height() if net else None,
            'wallet_ready': wallet is not None,
            'wallet_synced': bool(wallet and wallet.is_up_to_date()),
            'wallet_error': self.wallet_error,
        }
