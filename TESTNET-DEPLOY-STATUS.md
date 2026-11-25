# Testnet deployment status — Aevum (chain 44005)

**Status (2026-11-25 ~02:45 UTC): BLOCKED — no testnet gas. Nothing deployed.
No contract address, no tx hashes. SUBMISSION.md PENDING fields intentionally
left untouched.**

## What was verified this run

- Foundry test suite: **19/19 tests pass** (`forge test`, contracts/).
- Aevum RPC live: `https://rpc-test.qday.info`, chainId `0xabe5` (44005),
  current block ~4,048,360. Explorer live: `https://explorer-test.qday.info`.
- Real tUSD8 confirmed on-chain: `0x456569838f1d43009A91ebA4263147Afd7233483`,
  name `tUSD8`, 6 decimals.
- Fresh test-only deployer wallet created (never a personal wallet):
  **0x88028cE6A0A77a6BfF2286Cd7A84CF46CB044CB6**
  Private key saved ONLY in `.testnet-wallet/deployer.json` (mode 600,
  git-ignored — never commit, never share). Balances: **0 tQDAY, 0 tUSD8**.

## The blocker — funding the fresh wallet

Every faucet route needs something this fresh test-only wallet cannot provide
without a human step:

1. **QDay Discord faucet (documented route, `/faucet` → 1 tQDAY + 5 tUSD8,
   once per Discord account/address)** — requires joining the QDay Discord,
   reacting in `#verify`, and running `/faucet`. No Discord access from this
   environment, and no account may be created with personal data. This is the
   route that works for a fresh wallet: it does NOT check any mainnet balance.
2. **Official web faucet `faucet.qday.info`** (5 tQDAY/24h, no gas needed) —
   **rejects fresh wallets**: the recipient must hold ~0.0001 ETH, or 1 USDT,
   or 1 USDC on **Ethereum mainnet**. A brand-new test-only wallet holds none
   by design, and no personal wallet may be used. Dead end for this wallet.
3. **In-docs faucet widget (`wiki.qday.info/guide/start/faucet`)** — calls the
   faucet contract `0xE5074CBbd046AFb491EB8692abD6cF7ECCC6dEE5` on-chain, so the
   claim itself costs gas the wallet does not have. Verified by `eth_call`:
   both observed claim selectors revert (`0x1e83409a(address)` →
   `0x9e87fac8`; `0x16c38b3c(uint256)` → `0x30cd7471`), matching the
   on-chain history where recent `0x1e83409a` claims all failed. The faucet
   contract currently holds **0 tQDAY**, so it could not pay out anyway.
4. **Third-party faucet `faucet.zalalena.com/qday`** — requires solving
   hCaptcha in a live browser; not completable from this environment.

## Exact next step (human, ~2 minutes)

From Discord (or any account that can reach it): join QDay Discord →
`#verify` react ✅ → `/faucet` with address
`0x88028cE6A0A77a6BfF2286Cd7A84CF46CB044CB6` → it returns 1 tQDAY + 5 tUSD8.
Then re-run from the repo root:

```bash
export PATH="$HOME/.foundry/bin:$PATH"
cd contracts && TOKEN=0x456569838f1d43009A91ebA4263147Afd7233483 \
  forge script script/Deploy.s.sol --rpc-url aevum --broadcast --legacy \
  --private-key "$(python3 -c "import json;print(json.load(open('../.testnet-wallet/deployer.json'))['data'][0]['private_key'])")"
```

Then run the end-to-end flow with `agent/agent.py` (create → pay → pay-gasless
→ watch/receipts), record the InvoiceRegistry address + deployment tx +
both InvoicePaid tx hashes in SUBMISSION.md, and commit/push.

## Rules respected

Testnet only. Zero real money. No SSN/EAD/ID/tax data entered anywhere.
No personal wallet touched. No account created anywhere.
