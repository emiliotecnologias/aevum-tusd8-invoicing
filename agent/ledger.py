"""SQLite reconciliation ledger for the invoicing agent.

The chain is the source of truth; this ledger is the agent's local mirror
used to (a) avoid re-processing events, (b) attach off-chain data (memo
text, client names) to on-chain invoice ids, and (c) track generated
receipts and reminder drafts so nothing is sent twice.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS invoices (
    id INTEGER PRIMARY KEY,
    issuer TEXT NOT NULL,
    payer TEXT NOT NULL,
    amount INTEGER NOT NULL,
    due_at INTEGER NOT NULL DEFAULT 0,
    memo_hash TEXT NOT NULL,
    memo_text TEXT NOT NULL DEFAULT '',
    client_name TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'Open',
    paid_by TEXT NOT NULL DEFAULT '',
    paid_at INTEGER NOT NULL DEFAULT 0,
    created_tx TEXT NOT NULL DEFAULT '',
    paid_tx TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS receipts (
    invoice_id INTEGER PRIMARY KEY,
    tx_hash TEXT NOT NULL,
    amount INTEGER NOT NULL,
    payer TEXT NOT NULL,
    issued_at INTEGER NOT NULL,
    body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id INTEGER NOT NULL,
    tone TEXT NOT NULL,
    drafted_at INTEGER NOT NULL,
    body TEXT NOT NULL,
    UNIQUE(invoice_id, tone)
);
"""


class Ledger:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # -------------------------------------------------------------- meta

    def get_meta(self, key: str, default: str = "") -> str:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self.conn.commit()

    @property
    def last_processed_block(self) -> int:
        return int(self.get_meta("last_processed_block", "-1"))

    @last_processed_block.setter
    def last_processed_block(self, block: int) -> None:
        self.set_meta("last_processed_block", str(block))

    # -------------------------------------------------------------- invoices

    def upsert_created(self, invoice_id: int, args: dict, tx_hash: str) -> None:
        self.conn.execute(
            """
            INSERT INTO invoices(id, issuer, payer, amount, due_at, memo_hash, status, created_tx)
            VALUES(?, ?, ?, ?, ?, ?, 'Open', ?)
            ON CONFLICT(id) DO NOTHING
            """,
            (
                invoice_id,
                args["issuer"],
                args["payer"],
                int(args["amount"]),
                int(args["dueAt"]),
                args["memoHash"],
                tx_hash,
            ),
        )
        self.conn.commit()

    def mark_paid(self, invoice_id: int, payer: str, amount: int, paid_at: int, tx_hash: str) -> None:
        self.conn.execute(
            "UPDATE invoices SET status = 'Paid', paid_by = ?, paid_at = ?, paid_tx = ? WHERE id = ?",
            (payer, paid_at, tx_hash, invoice_id),
        )
        self.conn.commit()

    def mark_cancelled(self, invoice_id: int) -> None:
        self.conn.execute("UPDATE invoices SET status = 'Cancelled' WHERE id = ?", (invoice_id,))
        self.conn.commit()

    def attach_offchain(self, invoice_id: int, memo_text: str, client_name: str) -> None:
        self.conn.execute(
            "UPDATE invoices SET memo_text = ?, client_name = ? WHERE id = ?",
            (memo_text, client_name, invoice_id),
        )
        self.conn.commit()

    def get_invoice(self, invoice_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
        return dict(row) if row else None

    def all_invoices(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM invoices ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def open_invoices(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM invoices WHERE status = 'Open' ORDER BY due_at, id"
        ).fetchall()
        return [dict(r) for r in rows]

    # -------------------------------------------------------------- receipts / reminders

    def save_receipt(self, invoice_id: int, tx_hash: str, amount: int, payer: str, issued_at: int, body: str) -> bool:
        cur = self.conn.execute(
            """
            INSERT INTO receipts(invoice_id, tx_hash, amount, payer, issued_at, body)
            VALUES(?, ?, ?, ?, ?, ?) ON CONFLICT(invoice_id) DO NOTHING
            """,
            (invoice_id, tx_hash, amount, payer, issued_at, body),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def all_receipts(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM receipts ORDER BY invoice_id").fetchall()
        return [dict(r) for r in rows]

    def save_reminder(self, invoice_id: int, tone: str, drafted_at: int, body: str) -> bool:
        cur = self.conn.execute(
            """
            INSERT INTO reminders(invoice_id, tone, drafted_at, body)
            VALUES(?, ?, ?, ?) ON CONFLICT(invoice_id, tone) DO NOTHING
            """,
            (invoice_id, tone, drafted_at, body),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def all_reminders(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM reminders ORDER BY invoice_id, id").fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        self.conn.close()
