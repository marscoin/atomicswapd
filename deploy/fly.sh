#!/usr/bin/env bash
# Deploy atomicswapd to fly.io. Safe to run again: every step checks first.
#
#   deploy/fly.sh <app-name> [region]
#
# Creates the app, a 3 GB volume and a random wallet password (generated
# here and stored as a fly secret; it is never printed), then deploys one
# machine and waits until it is healthy. Creating the wallet and going
# live are separate, deliberate steps; see AGENTS.md.
set -euo pipefail

APP="${1:?usage: deploy/fly.sh <app-name> [region]}"
REGION="${2:-lax}"
cd "$(dirname "$0")/.."

command -v fly >/dev/null || { echo "install flyctl: https://fly.io/docs/flyctl/install/"; exit 1; }
fly auth whoami >/dev/null || { echo "log in first: fly auth login"; exit 1; }

if fly status -a "$APP" >/dev/null 2>&1; then
  echo "app $APP exists"
else
  fly apps create "$APP"
fi

if fly volumes list -a "$APP" 2>/dev/null | grep -q maker_data; then
  echo "volume maker_data exists"
else
  fly volumes create maker_data --app "$APP" --region "$REGION" --size 3 --yes
fi

if fly secrets list -a "$APP" 2>/dev/null | grep -q ATOMICSWAPD_WALLET_PASSWORD; then
  echo "wallet password secret exists"
else
  # Generated in a subshell and handed straight to fly; never shown.
  fly secrets set --stage -a "$APP" ATOMICSWAPD_WALLET_PASSWORD="$(openssl rand -hex 32)" >/dev/null
  echo "wallet password secret created"
fi

fly deploy --remote-only --ha=false --app "$APP" --primary-region "$REGION" --yes
fly scale count 1 --app "$APP" --yes >/dev/null   # never two makers on one wallet

URL="https://$APP.fly.dev"
echo "waiting for $URL/healthz"
for _ in $(seq 1 30); do
  if curl -fsS --max-time 5 "$URL/healthz" >/dev/null 2>&1; then
    echo "up: $URL"
    echo "next: check the stage with  curl -s $URL/api/snapshot | jq .setup"
    exit 0
  fi
  sleep 5
done
echo "not healthy yet; inspect with: fly logs -a $APP"
exit 1
