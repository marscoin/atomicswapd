Deploying or operating a maker: follow [AGENTS.md](AGENTS.md); its hard rules about seeds, passwords and going live apply.

Developing: Python 3.11+, Electrum-Mars 4.4.2+ installed alongside (`pip install -e ../electrum-mars`), `pytest` for tests, `atomicswapd --no-node` for the dashboard without a wallet, `web-src/` for the React app (`npm run build` writes into `atomicswapd/web`). Swap protocol changes belong in Electrum-Mars, not here.
