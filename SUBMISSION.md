# DoraHacks Submission — Aevum Builder Challenge #1

Copy-paste text for the DoraHacks submission form
(https://dorahacks.io/hackathon/qday). Deadline: **Fri 11 Dec 2026, 16:00 HKT**
(08:00 UTC / 03:00 EST Miami). Submit early — the portal gets slow at the end.

---

## Project name

Aevum tUSD8 Invoicing — Emilio Tecnologías

## Track

Invoice Payments (primary) + Native AI Assistants (Python reconciliation agent)

## One-line summary

Gasless tUSD8 invoicing for freelancers: on-chain invoices with exact amounts
and direct settlement, EIP-3009 signature payments (client needs no gas), and
a Python agent that reconciles chain events, issues receipts, and drafts
overdue-payment reminders automatically.

## Problem

Freelancers and small service businesses lose time and money to manual
invoicing: create the invoice in one tool, chase payment in another,
reconcile by hand, and remind clients who forget. Plain stablecoin transfers
settle fast but carry no invoice semantics — no amount due, no due date, no
receipt, no reminder, and nothing stopping a "payment" of 0.000001 tokens
from being recorded as paid.

## Solution

- **InvoiceRegistry (Solidity, Foundry, Shanghai EVM):** the issuer creates
  an invoice with an exact on-chain amount due, optional named payer, due
  date, and a hash of the off-chain document. Payment always moves exactly
  the stored amount, payer → issuer, in one transaction (direct settlement,
  no escrow, no custody).
- **Gasless payments (EIP-3009):** the client signs a
  `ReceiveWithAuthorization` off-chain; a relayer submits it and pays gas.
  The signed nonce is bound to a single invoice, preventing replay.
- **Python agent:** polls `InvoiceCreated/InvoicePaid/InvoiceCancelled`
  events with chunked `eth_getLogs` (Aevum's 1,000-block limit; HTTPS
  polling, no WebSocket on Aevum), reconciles into SQLite, generates a
  receipt per settled invoice, drafts escalating reminders
  (friendly/firm/final) for due and overdue invoices, and renders an HTML
  dashboard + JSON summary.

## What was built during the challenge

- `InvoiceRegistry.sol` — original contract, zero external Solidity
  dependencies (no OpenZeppelin/forge-std), `solc 0.8.24`, Shanghai EVM.
- `MockUSD8.sol` — local tUSD8 stand-in written from the ERC-20 / EIP-712 /
  EIP-3009 / EIP-2612 specs (same 6 decimals and `tUSD8`/`2` domain as the
  real token), used for local testing only.
- Foundry test suite: **19 tests passing**, including a fuzz test and
  EIP-3009 attack cases (relayer cannot alter the amount, nonce bound to
  invoice, replay/expiry/wrong-payer reverts).
- Python agent (web3.py): CLI with `watch`, `create`, `pay`, `pay-gasless`,
  `remind`, `receipts`, `report`.
- End-to-end local demo on anvil (Shanghai hardfork): deploy → 3 invoices →
  1 standard payment → 1 gasless payment → reconciliation → 2 receipts →
  1 overdue reminder draft → dashboard. Final balances verify direct
  settlement (registry holds 0).

## Links

- **GitHub repo (public, MIT):** https://github.com/emiliotecnologias/aevum-tusd8-invoicing
- **Demo video (2:48, real terminal output):** `demo/demo-video.mp4` in the repo
- **Sample dashboard:** `web/dashboard-sample.html` in the repo
- **Run it yourself:** `bash demo/run_demo.sh` (see README)

## On-chain evidence (Aevum testnet, chain 44005)

**Status: deployment scripts ready; live testnet deployment PENDING OWNER
DECISION.** The project owner handles all wallets personally and will
execute the deployment with a fresh test-only wallet funded from the QDay
Discord faucet, then fill in:

- InvoiceRegistry address: _to be added after deployment_
- Deployment tx: _to be added_
- Sample InvoicePaid tx (standard): _to be added_
- Sample InvoicePaid tx (gasless): _to be added_
- Deployed commit: _to be added (link to exact commit)_

Local evidence available now: full test suite + demo transcript
(`demo/demo-transcript.txt`) + demo video, all reproducible from the README.

## Tech stack

Solidity 0.8.24 · Foundry (forge/anvil, Shanghai EVM) · Python 3.12 ·
web3.py · eth-account · SQLite · ffmpeg (demo video)

## Team

Emilio Tecnologías (solo builder, Miami) — Python, automation, APIs, bots.

## Disclosures

- **AI-assisted development:** built with AI assistance (Muse) under the
  owner's direction.
- **Starter kit credit:** chain parameters and EIP-3009 design patterns
  reference the official Aevum Starter Kit (qday-io/aevum-starter-kit, MIT).
  All code in this repo was written from scratch; no starter-kit code is
  copied.
- **Testnet only.** No real funds anywhere. No identity/tax information
  entered anywhere. Note: the brief's "prize distributions are subject to
  eligibility checks" is unspecified — if claiming a prize ever requires
  SSN/ITIN/W-9, the owner will stop and decide personally before providing
  anything.

## Pre-submission checklist (owner)

- [ ] Register on DoraHacks for Aevum Builder Challenge #1 (read the Terms tab first)
- [ ] Decide on testnet deployment (fresh test wallet + Discord faucet)
- [ ] If deployed: fill in contract address + tx hashes above and in README
- [ ] Verify repo opens in a logged-out browser
- [ ] Submit before 11 Dec 2026 16:00 HKT; top 5 pitch live 15 Dec
