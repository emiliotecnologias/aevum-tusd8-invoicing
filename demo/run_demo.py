#!/usr/bin/env python3
"""End-to-end local demo: deploy -> invoice -> pay (standard + gasless)
-> agent reconciliation -> receipts -> overdue reminder -> dashboard.

Runs against a local anvil node with the Shanghai hardfork (mirrors Aevum
testnet). Uses anvil's public default test accounts — never real keys.

Prerequisites: `forge build` in contracts/ and anvil running on :8545
(see run_demo.sh, which handles both).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "agent"))

from chain import Chain, fmt_amount  # noqa: E402

# Anvil default test accounts (public, well-known — local use only).
ISSUER_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
PAYER_KEY = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"
RELAYER_KEY = "0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a"
ISSUER = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
PAYER = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"

RPC = os.environ.get("RPC_URL", "http://127.0.0.1:8545")
PY = sys.executable


def section(title: str) -> None:
    print(f"\n{'=' * 64}\n{title}\n{'=' * 64}", flush=True)


def run_agent(env: dict, *cli_args: str) -> None:
    cmd = [PY, str(REPO / "agent" / "agent.py"), *cli_args]
    print(f"$ python agent/agent.py {' '.join(cli_args)}", flush=True)
    subprocess.run(cmd, env=env, cwd=REPO / "demo", check=True)


def main() -> int:
    section("1. Deploy MockUSD8 + InvoiceRegistry to local anvil (Shanghai)")
    chain = Chain(RPC)
    print(f"connected: chain_id={chain.chain_id}, block={chain.w3.eth.block_number}")
    token_addr = chain.deploy_mock_token(ISSUER_KEY)
    registry_addr = chain.deploy_registry(ISSUER_KEY, token_addr)
    print(f"MockUSD8 (tUSD8 stand-in): {token_addr}")
    print(f"InvoiceRegistry:           {registry_addr}")

    chain.mint_mock(ISSUER_KEY, PAYER, 1_000_000_000)
    print(f"minted 1,000.00 tUSD8 to payer {PAYER}")

    env = {
        **os.environ,
        "RPC_URL": RPC,
        "REGISTRY_ADDRESS": registry_addr,
        "TOKEN_ADDRESS": token_addr,
        "LEDGER_DB": str(REPO / "demo" / "ledger.db"),
        "OUTBOX_DIR": str(REPO / "demo" / "outbox"),
        "REPORT_DIR": str(REPO / "demo" / "report"),
        "ISSUER_PRIVATE_KEY": ISSUER_KEY,
        "PAYER_PRIVATE_KEY": PAYER_KEY,
        "RELAYER_PRIVATE_KEY": RELAYER_KEY,
    }

    section("2. Issuer creates three invoices")
    run_agent(env, "create", "--amount", "25.50", "--payer", PAYER,
              "--memo", "Video editing - 5 short clips", "--client", "Acme Media", "--due-in-days", "7")
    run_agent(env, "create", "--amount", "120.00", "--payer", PAYER,
              "--memo", "Landing page design + build", "--client", "Acme Media", "--due-in-days", "3")
    run_agent(env, "create", "--amount", "45.00", "--payer", PAYER,
              "--memo", "Subtitles EN to ES - 3 videos", "--client", "Beta Studio", "--due-in-days", "-2")

    section("3. Client pays invoice #1 the standard way (approve + pay)")
    run_agent(env, "pay", "--invoice", "1")

    section("4. Client pays invoice #2 GASLESSLY (EIP-3009 signature, relayer submits)")
    run_agent(env, "pay-gasless", "--invoice", "2")

    section("5. Agent reconciles chain events and generates receipts")
    run_agent(env, "watch")
    run_agent(env, "receipts")

    section("6. Agent drafts a reminder for overdue invoice #3")
    run_agent(env, "remind")
    reminder_file = REPO / "demo" / "outbox" / "reminder-invoice-3-firm.txt"
    if reminder_file.exists():
        print(f"\n--- {reminder_file.name} ---")
        print(reminder_file.read_text())

    section("7. Agent builds the dashboard report")
    run_agent(env, "report")

    section("8. Final balances (direct settlement, registry holds nothing)")
    print(f"issuer  {ISSUER}: {fmt_amount(chain.token_balance(ISSUER))} tUSD8")
    print(f"payer   {PAYER}: {fmt_amount(chain.token_balance(PAYER))} tUSD8")
    print(f"registry {registry_addr}: {fmt_amount(chain.token_balance(registry_addr))} tUSD8")
    for inv_id in (1, 2, 3):
        inv = chain.get_invoice(inv_id)
        print(f"invoice #{inv_id}: {inv['status']}, amount={fmt_amount(inv['amount'])} tUSD8, paidBy={inv['paidBy']}")

    print("\nDEMO COMPLETE — all flows executed against a real local chain.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
