#!/usr/bin/env python3
"""Aevum tUSD8 invoicing agent (Emilio Tecnologías).

An off-chain assistant for the InvoiceRegistry contract:

* ``watch``    — poll the chain for InvoiceCreated / InvoicePaid /
                 InvoiceCancelled events (chunked eth_getLogs, Aevum-safe),
                 reconcile them into a local SQLite ledger, and generate a
                 payment receipt for every settled invoice.
* ``create``   — create an invoice on-chain and attach off-chain details
                 (client name, memo text) locally; only the memo *hash*
                 goes on-chain.
* ``pay``      — standard payment (approve + pay).
* ``pay-gasless`` — payer signs an EIP-3009 authorization off-chain; a
                 relayer submits it and pays the gas (payer needs no tQDAY).
* ``remind``   — draft payment reminders for open invoices that are due
                 soon or overdue. Drafting is deterministic and offline
                 (template-based assistant, no external AI API, no cost);
                 drafts land in the outbox for human review before sending.
* ``report``   — write a static HTML dashboard + JSON summary from the ledger.

Configuration is via environment variables (never hardcode keys):

    RPC_URL            default http://127.0.0.1:8545
    REGISTRY_ADDRESS   InvoiceRegistry address
    TOKEN_ADDRESS      tUSD8 (or MockUSD8) address
    LEDGER_DB          default ./ledger.db
    EXPLORER_BASE      default https://explorer-test.qday.info
    ISSUER_PRIVATE_KEY / PAYER_PRIVATE_KEY / RELAYER_PRIVATE_KEY
                       only needed for the matching subcommands

Testnet / local keys only. Never point this at mainnet funds.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from chain import Chain, fmt_amount, parse_amount  # noqa: E402
from ledger import Ledger  # noqa: E402

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


def ts_to_iso(ts: int) -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def make_chain(args) -> Chain:
    return Chain(
        rpc_url=os.environ.get("RPC_URL", "http://127.0.0.1:8545"),
        registry_address=os.environ.get("REGISTRY_ADDRESS"),
        token_address=os.environ.get("TOKEN_ADDRESS"),
    )


def make_ledger() -> Ledger:
    return Ledger(os.environ.get("LEDGER_DB", "ledger.db"))


def explorer_tx(tx_hash: str) -> str:
    base = os.environ.get("EXPLORER_BASE", "https://explorer-test.qday.info").rstrip("/")
    return f"{base}/tx/{tx_hash}"


# ------------------------------------------------------------------ receipts


def build_receipt(invoice: dict, tx_hash: str, paid_at: int) -> str:
    amount = fmt_amount(invoice["amount"])
    client = invoice.get("client_name") or invoice["payer"]
    memo = invoice.get("memo_text") or "(see on-chain memo hash)"
    return (
        f"PAYMENT RECEIPT — Emilio Tecnologías\n"
        f"{'-' * 52}\n"
        f"Invoice:      #{invoice['id']}\n"
        f"Client:       {client}\n"
        f"Description:  {memo}\n"
        f"Amount:       {amount} tUSD8\n"
        f"Paid by:      {invoice['paid_by'] or invoice['payer']}\n"
        f"Paid at:      {ts_to_iso(paid_at)}\n"
        f"Transaction:  {tx_hash}\n"
        f"Explorer:     {explorer_tx(tx_hash)}\n"
        f"Status:       SETTLED (direct payer -> issuer, no escrow)\n"
    )


# ------------------------------------------------------------------ reminders


def reminder_tone(invoice: dict, now: int) -> str | None:
    """Pick a reminder tone from how close/past the due date we are."""
    due = invoice["due_at"]
    if not due:
        return None
    if now > due + 7 * 86400:
        return "final"
    if now > due:
        return "firm"
    if due - now <= 3 * 86400:
        return "friendly"
    return None


def draft_reminder(invoice: dict, tone: str, now: int) -> str:
    amount = fmt_amount(invoice["amount"])
    client = invoice.get("client_name") or "there"
    memo = invoice.get("memo_text") or "your invoice"
    due_str = ts_to_iso(invoice["due_at"])
    days_over = max(0, (now - invoice["due_at"]) // 86400)
    if tone == "friendly":
        subject = f"Friendly reminder: invoice #{invoice['id']} due {due_str}"
        body = (
            f"Hi {client},\n\n"
            f"Just a friendly heads-up that invoice #{invoice['id']} ({memo}) "
            f"for {amount} tUSD8 is due on {due_str}. You can settle it in one "
            f"signature — no gas needed on your side.\n\n"
            f"Thanks!\nEmilio Tecnologías"
        )
    elif tone == "firm":
        subject = f"Overdue: invoice #{invoice['id']} ({amount} tUSD8)"
        body = (
            f"Hi {client},\n\n"
            f"Invoice #{invoice['id']} ({memo}) for {amount} tUSD8 was due on "
            f"{due_str} and is now {days_over} day(s) overdue. Please settle it "
            f"as soon as possible, or let me know if something is wrong.\n\n"
            f"Emilio Tecnologías"
        )
    else:  # final
        subject = f"Final notice: invoice #{invoice['id']} unpaid ({amount} tUSD8)"
        body = (
            f"Hi {client},\n\n"
            f"Final notice: invoice #{invoice['id']} ({memo}) for {amount} tUSD8 "
            f"remains unpaid {days_over} day(s) after its due date ({due_str}). "
            f"Please pay immediately or contact me to arrange settlement.\n\n"
            f"Emilio Tecnologías"
        )
    return f"Subject: {subject}\n\n{body}\n"


# ------------------------------------------------------------------ commands


def cmd_watch(args) -> int:
    chain = make_chain(args)
    ledger = make_ledger()
    head = chain.w3.eth.block_number
    start = ledger.last_processed_block + 1
    if start > head:
        print(f"watch: nothing new (head block {head}, already processed)")
        return 0
    events = chain.fetch_events(start, head)
    new_receipts = 0
    for ev in events:
        a = ev["args"]
        if ev["event"] == "InvoiceCreated":
            ledger.upsert_created(int(a["id"]), a, ev["transactionHash"])
            print(f"  [block {ev['blockNumber']}] InvoiceCreated #{a['id']} "
                  f"amount={fmt_amount(int(a['amount']))} tUSD8 payer={a['payer']}")
        elif ev["event"] == "InvoicePaid":
            inv_id = int(a["id"])
            block_ts = chain.w3.eth.get_block(ev["blockNumber"])["timestamp"]
            ledger.mark_paid(inv_id, a["payer"], int(a["amount"]), block_ts, ev["transactionHash"])
            invoice = ledger.get_invoice(inv_id)
            if invoice:
                invoice["paid_by"] = a["payer"]
                body = build_receipt(invoice, ev["transactionHash"], block_ts)
                if ledger.save_receipt(inv_id, ev["transactionHash"], int(a["amount"]), a["payer"], block_ts, body):
                    new_receipts += 1
                    print(f"  [block {ev['blockNumber']}] InvoicePaid #{inv_id} "
                          f"amount={fmt_amount(int(a['amount']))} tUSD8 -> receipt generated")
        elif ev["event"] == "InvoiceCancelled":
            ledger.mark_cancelled(int(a["id"]))
            print(f"  [block {ev['blockNumber']}] InvoiceCancelled #{a['id']}")
    ledger.last_processed_block = head
    print(f"watch: processed blocks {start}..{head}, {len(events)} event(s), {new_receipts} new receipt(s)")
    ledger.close()
    return 0


def cmd_create(args) -> int:
    chain = make_chain(args)
    key = os.environ.get("ISSUER_PRIVATE_KEY")
    if not key:
        print("ISSUER_PRIVATE_KEY is not set", file=sys.stderr)
        return 2
    due_at = int(time.time()) + args.due_in_days * 86400 if args.due_in_days is not None else 0
    invoice_id, tx_hash = chain.create_invoice(
        key, args.payer or ZERO_ADDRESS, parse_amount(args.amount), due_at, args.memo
    )
    ledger = make_ledger()
    # Mirror the on-chain invoice into the ledger immediately (watch would
    # also do this later), then attach the off-chain details so receipts
    # and reminders can use the client name and memo text.
    onchain = chain.get_invoice(invoice_id)
    ledger.upsert_created(invoice_id, onchain, tx_hash)
    ledger.attach_offchain(invoice_id, args.memo, args.client or "")
    ledger.close()
    print(f"created invoice #{invoice_id}: {args.amount} tUSD8, payer={args.payer or 'anyone'}, "
          f"due={ts_to_iso(due_at)}, tx={tx_hash}")
    return 0


def cmd_pay(args) -> int:
    chain = make_chain(args)
    key = os.environ.get("PAYER_PRIVATE_KEY")
    if not key:
        print("PAYER_PRIVATE_KEY is not set", file=sys.stderr)
        return 2
    invoice = chain.get_invoice(args.invoice)
    tx_hash = chain.pay_standard(key, args.invoice, invoice["amount"])
    print(f"paid invoice #{args.invoice} ({fmt_amount(invoice['amount'])} tUSD8), tx={tx_hash}")
    return 0


def cmd_pay_gasless(args) -> int:
    chain = make_chain(args)
    payer_key = os.environ.get("PAYER_PRIVATE_KEY")
    relayer_key = os.environ.get("RELAYER_PRIVATE_KEY")
    if not payer_key or not relayer_key:
        print("PAYER_PRIVATE_KEY and RELAYER_PRIVATE_KEY must be set", file=sys.stderr)
        return 2
    invoice = chain.get_invoice(args.invoice)
    now = int(time.time())
    auth = chain.sign_receive_authorization(
        payer_key, args.invoice, invoice["amount"], valid_after=now - 60, valid_before=now + 3600
    )
    print(f"payer {auth['from']} signed EIP-3009 authorization for invoice #{args.invoice} "
          f"({fmt_amount(invoice['amount'])} tUSD8) — payer spent no gas")
    tx_hash = chain.submit_gasless(relayer_key, args.invoice, auth)
    print(f"relayer submitted gasless payment, tx={tx_hash}")
    return 0


def cmd_remind(args) -> int:
    ledger = make_ledger()
    now = int(time.time())
    drafted = 0
    outbox = Path(os.environ.get("OUTBOX_DIR", "outbox"))
    outbox.mkdir(parents=True, exist_ok=True)
    for invoice in ledger.open_invoices():
        tone = reminder_tone(invoice, now)
        if tone is None:
            continue
        body = draft_reminder(invoice, tone, now)
        if ledger.save_reminder(invoice["id"], tone, now, body):
            path = outbox / f"reminder-invoice-{invoice['id']}-{tone}.txt"
            path.write_text(body)
            drafted += 1
            print(f"drafted {tone} reminder for invoice #{invoice['id']} -> {path}")
    print(f"remind: {drafted} new draft(s) in {outbox}/ (review before sending)")
    ledger.close()
    return 0


def cmd_receipts(args) -> int:
    ledger = make_ledger()
    receipts = ledger.all_receipts()
    for r in receipts:
        print(r["body"])
    print(f"receipts: {len(receipts)} total")
    ledger.close()
    return 0


def cmd_report(args) -> int:
    ledger = make_ledger()
    invoices = ledger.all_invoices()
    receipts = ledger.all_receipts()
    reminders = ledger.all_reminders()
    paid = [i for i in invoices if i["status"] == "Paid"]
    open_inv = [i for i in invoices if i["status"] == "Open"]
    total_paid = sum(i["amount"] for i in paid)
    total_open = sum(i["amount"] for i in open_inv)

    summary = {
        "generated_at": ts_to_iso(int(time.time())),
        "invoices_total": len(invoices),
        "invoices_paid": len(paid),
        "invoices_open": len(open_inv),
        "total_settled_tusd8": fmt_amount(total_paid),
        "total_outstanding_tusd8": fmt_amount(total_open),
        "receipts": len(receipts),
        "reminders_drafted": len(reminders),
    }
    out_dir = Path(os.environ.get("REPORT_DIR", "report"))
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")

    rows = "".join(
        f"<tr><td>#{i['id']}</td><td>{i['client_name'] or i['payer'][:10] + '…'}</td>"
        f"<td>{i['memo_text'] or '—'}</td><td>{fmt_amount(i['amount'])} tUSD8</td>"
        f"<td>{i['status']}</td><td>{ts_to_iso(i['due_at'])}</td></tr>"
        for i in invoices
    )
    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Emilio Tecnologías — tUSD8 Invoicing Dashboard</title>
<style>body{{font-family:system-ui,sans-serif;margin:2rem;background:#0f1115;color:#e8e8e8}}
table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #333;padding:.5rem;text-align:left}}
th{{background:#1c1f26}}.stat{{display:inline-block;margin-right:2rem}}.stat b{{font-size:1.4rem}}</style>
</head><body>
<h1>tUSD8 Invoicing — Emilio Tecnologías</h1>
<p>Generated {summary['generated_at']} · Aevum testnet (chain 44005) · test tokens only</p>
<p><span class="stat">Settled<br><b>{summary['total_settled_tusd8']} tUSD8</b></span>
<span class="stat">Outstanding<br><b>{summary['total_outstanding_tusd8']} tUSD8</b></span>
<span class="stat">Paid invoices<br><b>{summary['invoices_paid']}/{summary['invoices_total']}</b></span>
<span class="stat">Receipts<br><b>{summary['receipts']}</b></span>
<span class="stat">Reminders drafted<br><b>{summary['reminders_drafted']}</b></span></p>
<table><tr><th>Invoice</th><th>Client</th><th>Description</th><th>Amount</th><th>Status</th><th>Due</th></tr>
{rows}</table>
</body></html>
"""
    (out_dir / "dashboard.html").write_text(html)
    print(f"report: wrote {out_dir}/dashboard.html and {out_dir}/summary.json")
    print(json.dumps(summary, indent=2))
    ledger.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Aevum tUSD8 invoicing agent")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("watch", help="reconcile chain events into the ledger + generate receipts")

    p = sub.add_parser("create", help="create an invoice on-chain")
    p.add_argument("--amount", required=True, help="amount in tUSD8, e.g. 25.50")
    p.add_argument("--payer", default="", help="payer address (empty = anyone may pay)")
    p.add_argument("--memo", required=True, help="invoice description (only its hash goes on-chain)")
    p.add_argument("--client", default="", help="client display name (off-chain only)")
    p.add_argument("--due-in-days", type=int, default=None, help="days until due (negative = already overdue)")

    p = sub.add_parser("pay", help="pay an invoice (approve + pay)")
    p.add_argument("--invoice", type=int, required=True)

    p = sub.add_parser("pay-gasless", help="pay via EIP-3009 signature + relayer")
    p.add_argument("--invoice", type=int, required=True)

    sub.add_parser("remind", help="draft reminders for due/overdue open invoices")
    sub.add_parser("receipts", help="print all generated receipts")
    sub.add_parser("report", help="write dashboard.html + summary.json")

    args = parser.parse_args()
    handlers = {
        "watch": cmd_watch,
        "create": cmd_create,
        "pay": cmd_pay,
        "pay-gasless": cmd_pay_gasless,
        "remind": cmd_remind,
        "receipts": cmd_receipts,
        "report": cmd_report,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
