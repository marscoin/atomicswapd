# AGENTS.md: deploying and running atomicswapd

Instructions for AI agents (and humans who like checklists) setting up a BTC ⇄ MARS market maker for an operator. Each step has a check; don't move on until it passes.

## Hard rules

1. **Never see, print, log or store the wallet seed or password.**
   - Create wallets with `--seed-file`, so the seed goes to a private file on the server.
   - Generate the password with `openssl rand` inside the command that stores it.
   - Don't `cat` the seed file, read secrets back, or echo environment variables.
2. **The operator decides about money.** Ask the operator for all of these; never choose them yourself:
   - the Bitcoin payout address
   - how much MARS to deposit
   - when to switch to live
3. **One instance per wallet.** Never scale above one machine or start a second container with the same volume.
4. **Don't go live until the dashboard's setup stage is `ready`** and the operator has approved the offers under **Would post now**.
5. **Don't edit the swap code here to make something work.** The protocol lives in [Electrum-Mars](https://github.com/marscoin/electrum-mars). Report problems instead.

## Inputs to collect from the operator

| Input | Example | Notes |
|---|---|---|
| App name | `alice-mars-maker` | Must be unique on fly.io. |
| Region | `lax`, `fra`, `sin` | Near the operator; optional. |
| Bitcoin payout address | `bc1q...` | Mainnet. Proceeds go here. |
| MARS to deposit | 3,000 | At least 667 with the default settings. |

## Procedure on fly.io

**1. Prerequisites.**

```sh
fly version && fly auth whoami
```

Check: both commands succeed. If not logged in, the operator runs `fly auth login` themselves.

**2. Deploy.** This step is idempotent.

```sh
deploy/fly.sh <app> [region]
```

Check: the script ends with `up: https://<app>.fly.dev`.

```sh
curl -s https://<app>.fly.dev/api/snapshot | jq .setup
```

Check: the stage is `needs_wallet`.

**3. Create the wallet.** The seed goes to a file, never to your output.

```sh
fly ssh console -a <app> -C "atomicswapd wallet create --seed-file /data/seed-backup.txt"
```

Check: the output names the seed file and a `mars1...` deposit address, and contains no seed words. Then tell the operator to fetch the seed themselves and delete the file:

```sh
fly ssh sftp get /data/seed-backup.txt -a <app>
fly ssh console -a <app> -C "rm /data/seed-backup.txt"
```

Wait until the operator confirms they have stored the seed offline.

**4. Load the wallet and set the payout address.**

```sh
fly apps restart <app>
fly secrets set ATOMICSWAPD_BTC_ADDRESS=<operator's bc1 address> -a <app>
```

Check: `atomicswapd status https://<app>.fly.dev --json` shows stage `syncing`.

On a new volume the node checks about 3.6 million headers. Shared CPUs are throttled under sustained load, so this takes about 4 hours. With the operator's OK (it costs a few cents), use a dedicated CPU while syncing and scale back afterwards:

```sh
fly scale vm performance-1x --memory 2048 -a <app>     # while the stage is syncing
fly scale vm shared-cpu-1x --memory 1024 -a <app>      # once it leaves syncing
```

**5. Fund.** Give the operator the deposit address from the status output.
- Suggest a small test amount first.
- Poll `atomicswapd status ... --json` every few minutes, not more often.

Check: the stage moves from `needs_funds` to `ready`.

**6. Review with the operator.** Show them the `would_post` offers from the status output: sizes, BTC prices, and spread over market. Ask which minimum spread they want. The default is 5%; tiers add +2 points each.

**7. Go live, only with the operator's approval.**

```sh
fly secrets set ATOMICSWAPD_FEE_PERCENT=<spread> ATOMICSWAPD_MODE=live -a <app>
```

Check: within a minute or two the stage is `live` and `offers_posted` is above 0. `atomicswapd status` exits with code 0 only when live. If there are no offers after a few minutes, read `.market.error`: without a fresh price the maker posts nothing.

## Reading the state

`GET /api/snapshot` → `.setup` always says what to do next:

| `stage` | Meaning | Action |
|---|---|---|
| `needs_wallet` | No wallet file | Step 3 |
| `wallet_error` | The wallet didn't open (often a wrong password) | Read `.next`; tell the operator. Don't create a new wallet over an existing one. |
| `syncing` | Downloading headers or wallet history (`.progress` %) | Wait |
| `needs_funds` | Inventory below `min_inventory_mars` | Step 5 |
| `needs_payout_address` | No BTC address set | Step 4 |
| `waiting_for_price` | No fresh market price | Wait; check `.market.error` |
| `ready` | Everything works; nothing posted (watch mode) | Steps 6–7 |
| `live` | Trading | Monitor |
| `maker_error` | The swap engine didn't start | Read `.next`; fix the configuration or report it |

Other useful fields:
- `.maker.swaps`: swaps in progress and finished, with transaction ids.
- `.relays`: the order-book servers' health.
- `.events`: recent activity.

Logs: `fly logs -a <app>`.

## Operating

- **Pause:** `fly secrets set ATOMICSWAPD_MODE=watch -a <app>`. Swaps in progress still finish or refund.
- **Update:** pull this repo and rerun `deploy/fly.sh <app>`. Headers, wallet and swap database live on the volume and survive deploys.
- **Change sizes or spread:** see `atomicswapd.example.toml`. Mount a config file through `ATOMICSWAPD_CONFIG`, or ask the operator before changing what is offered.
- **Never** delete the volume or the wallet while `.maker.swaps` has anything in state `mars_locked` or `btc_locked`.

## Without fly.io

Use the Docker procedure in the README. The checks are the same; run `docker exec <container> atomicswapd status --json`.
