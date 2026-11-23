#!/usr/bin/env bash
# Full local demo: build contracts, start anvil (Shanghai hardfork, like
# Aevum testnet), run the end-to-end Python demo, then stop anvil.
# Output is saved to demo-transcript.txt (real output, used for the video).
set -euo pipefail

export PATH="$HOME/.foundry/bin:$PATH"
cd "$(dirname "$0")/.."

echo "== forge build =="
(cd contracts && forge build --quiet)

echo "== starting anvil (shanghai) on :8545 =="
anvil --hardfork shanghai --port 8545 --silent &
ANVIL_PID=$!
trap 'kill $ANVIL_PID 2>/dev/null || true' EXIT
sleep 2

rm -f demo/ledger.db
rm -rf demo/outbox demo/report

echo "== running demo =="
.venv/bin/python demo/run_demo.py 2>&1 | tee demo/demo-transcript.txt
