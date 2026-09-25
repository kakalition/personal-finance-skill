#!/usr/bin/env python3
"""Record, transfer, list, edit, and delete transactions.

Amounts are passed as a positive magnitude plus a direction. Use
``--direction out`` for money leaving an account and ``--direction in`` for
money arriving. When ``--category`` is given and its kind is known, the
direction is derived automatically (expense => out, income => in).
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import uuid
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _lib  # noqa: E402


def _normalize_tags(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    parts = [part.strip() for part in str(value).split(",")]
    parts = [part for part in parts if part]
    return ",".join(parts) if parts else None


def _tags_list(value: Optional[str]) -> List[str]:
    if not value:
        return []
    return [part for part in value.split(",") if part]


def _tx_dict(row: sqlite3.Row, code: str) -> Dict[str, Any]:
    units = int(row["amount_units"])
    return {
        "id": row["id"],
        "date": row["date"],
        "account_id": row["account_id"],
        "account": row["account_name"] if "account_name" in row.keys() else None,
        "amount_units": units,
        "amount": _lib.fmt_amount(units, code),
        "direction": "out" if units < 0 else "in",
        "category_id": row["category_id"],
        "category": row["category_name"] if "category_name" in row.keys() else None,
        "payee": row["payee"],
        "note": row["note"],
        "tags": _tags_list(row["tags"]),
        "transfer_group": row["transfer_group"],
    }


def _fetch_tx(conn: sqlite3.Connection, tx_id: int) -> sqlite3.Row:
    row = conn.execute(
        "SELECT t.*, a.name AS account_name, c.name AS category_name "
        "FROM transactions t JOIN accounts a ON a.id = t.account_id "
        "LEFT JOIN categories c ON c.id = t.category_id WHERE t.id = ?",
        (tx_id,),
    ).fetchone()
    if row is None:
        raise _lib.CommandError("not_found", "no transaction with id %s" % tx_id)
    return row


def cmd_add(conn: sqlite3.Connection, args: argparse.Namespace) -> Dict[str, Any]:
    code = _lib.resolve_currency(conn, args.currency)
    account = _lib.resolve_account(conn, args.account)
    category = None
    if args.category:
        category = _lib.resolve_category(conn, args.category)
    direction = _lib.infer_direction(category, args.direction)
    if direction is None:
        raise _lib.CommandError(
            "missing_direction",
            "provide --category (income or expense) or --direction in|out",
        )
    magnitude = _lib.parse_amount(args.amount, _lib.currency_exponent(code), allow_negative=False)
    units = _lib.apply_direction(magnitude, direction)
    tx_date = _lib.validate_date(args.date)
    cur = conn.execute(
        "INSERT INTO transactions (account_id, date, amount_units, category_id, payee, note, tags) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            account["id"],
            tx_date,
            units,
            category["id"] if category else None,
            args.payee,
            args.note,
            _normalize_tags(args.tags),
        ),
    )
    return _tx_dict(_fetch_tx(conn, cur.lastrowid), code)


def cmd_transfer(conn: sqlite3.Connection, args: argparse.Namespace) -> Dict[str, Any]:
    code = _lib.resolve_currency(conn, args.currency)
    src = _lib.resolve_account(conn, args.from_account, field="source account")
    dst = _lib.resolve_account(conn, args.to_account, field="destination account")
    if src["id"] == dst["id"]:
        raise _lib.CommandError("invalid_input", "transfer source and destination must differ")
    magnitude = _lib.parse_amount(args.amount, _lib.currency_exponent(code), allow_negative=False)
    tx_date = _lib.validate_date(args.date)
    group = uuid.uuid4().hex
    debit = conn.execute(
        "INSERT INTO transactions (account_id, date, amount_units, note, transfer_group) "
        "VALUES (?, ?, ?, ?, ?)",
        (src["id"], tx_date, -magnitude, args.note, group),
    ).lastrowid
    credit = conn.execute(
        "INSERT INTO transactions (account_id, date, amount_units, note, transfer_group) "
        "VALUES (?, ?, ?, ?, ?)",
        (dst["id"], tx_date, magnitude, args.note, group),
    ).lastrowid
    return {
        "transfer_group": group,
        "date": tx_date,
        "amount": _lib.fmt_amount(magnitude, code),
        "amount_units": magnitude,
        "from": {"id": src["id"], "name": src["name"], "transaction_id": debit},
        "to": {"id": dst["id"], "name": dst["name"], "transaction_id": credit},
        "note": "Transfers are excluded from income and expense reports.",
    }


def cmd_list(conn: sqlite3.Connection, args: argparse.Namespace) -> List[Dict[str, Any]]:
    code = _lib.resolve_currency(conn, args.currency)
    sql = (
        "SELECT t.*, a.name AS account_name, c.name AS category_name "
        "FROM transactions t JOIN accounts a ON a.id = t.account_id "
        "LEFT JOIN categories c ON c.id = t.category_id"
    )
    clauses: List[str] = []
    params: List[Any] = []
    if args.account:
        clauses.append("t.account_id = ?")
        params.append(_lib.resolve_account(conn, args.account)["id"])
    if args.from_date:
        clauses.append("t.date >= ?")
        params.append(_lib.validate_date(args.from_date, "--from"))
    if args.to_date:
        clauses.append("t.date <= ?")
        params.append(_lib.validate_date(args.to_date, "--to"))
    if args.category:
        clauses.append("t.category_id = ?")
        params.append(_lib.resolve_category(conn, args.category)["id"])
    if args.search:
        clauses.append("(t.payee LIKE ? OR t.note LIKE ? OR t.tags LIKE ?)")
        needle = "%%%s%%" % args.search
        params.extend([needle, needle, needle])
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY t.date DESC, t.id DESC"
    if args.limit is not None:
        if args.limit < 0:
            raise _lib.CommandError("invalid_input", "--limit must be zero or greater")
        sql += " LIMIT ?"
        params.append(args.limit)
    rows = conn.execute(sql, params).fetchall()
    return [_tx_dict(row, code) for row in rows]


def cmd_edit(conn: sqlite3.Connection, args: argparse.Namespace) -> Dict[str, Any]:
    code = _lib.resolve_currency(conn, args.currency)
    row = _fetch_tx(conn, args.transaction)
    if row["transfer_group"]:
        raise _lib.CommandError(
            "invalid_input",
            "transaction %s is part of a transfer; delete it and create a new transfer instead"
            % args.transaction,
        )
    fields: Dict[str, Any] = {}
    if args.account:
        fields["account_id"] = _lib.resolve_account(conn, args.account)["id"]
    if args.date:
        fields["date"] = _lib.validate_date(args.date)
    if args.category:
        fields["category_id"] = _lib.resolve_category(conn, args.category)["id"]
    if args.payee is not None:
        fields["payee"] = args.payee
    if args.note is not None:
        fields["note"] = args.note
    if args.tags is not None:
        fields["tags"] = _normalize_tags(args.tags)

    if args.amount is not None:
        magnitude = _lib.parse_amount(
            args.amount, _lib.currency_exponent(code), allow_negative=False
        )
        category = None
        if fields.get("category_id") is not None:
            category = _lib.resolve_category(conn, fields["category_id"])
        elif row["category_id"] is not None:
            category = _lib.resolve_category(conn, row["category_id"])
        direction = _lib.infer_direction(category, args.direction)
        if direction is None:
            direction = "out" if int(row["amount_units"]) < 0 else "in"
        fields["amount_units"] = _lib.apply_direction(magnitude, direction)
    elif args.direction:
        direction = _lib.infer_direction(None, args.direction)
        fields["amount_units"] = _lib.apply_direction(abs(int(row["amount_units"])), direction)
    elif fields.get("category_id") is not None:
        category = _lib.resolve_category(conn, fields["category_id"])
        desired = _lib.infer_direction(category, None)
        units = int(row["amount_units"])
        if desired == "out" and units > 0:
            fields["amount_units"] = -units
        elif desired == "in" and units < 0:
            fields["amount_units"] = abs(units)

    if not fields:
        raise _lib.CommandError("invalid_input", "nothing to update; pass at least one field")
    assignments = ", ".join("%s = ?" % key for key in fields)
    conn.execute(
        "UPDATE transactions SET %s WHERE id = ?" % assignments,
        list(fields.values()) + [row["id"]],
    )
    return _tx_dict(_fetch_tx(conn, row["id"]), code)


def cmd_delete(conn: sqlite3.Connection, args: argparse.Namespace) -> Dict[str, Any]:
    code = _lib.resolve_currency(conn, args.currency)
    row = _fetch_tx(conn, args.transaction)
    if row["transfer_group"]:
        ids = [
            r["id"]
            for r in conn.execute(
                "SELECT id FROM transactions WHERE transfer_group = ? ORDER BY id",
                (row["transfer_group"],),
            ).fetchall()
        ]
        conn.execute("DELETE FROM transactions WHERE transfer_group = ?", (row["transfer_group"],))
        return {
            "deleted": True,
            "transfer_group": row["transfer_group"],
            "deleted_transaction_ids": ids,
            "count": len(ids),
            "note": "Both sides of the transfer were deleted.",
        }
    conn.execute("DELETE FROM transactions WHERE id = ?", (row["id"],))
    return {"deleted": True, "deleted_transaction_ids": [row["id"]], "count": 1}


def build_parser() -> argparse.ArgumentParser:
    common = _lib.common_parser()
    parser = argparse.ArgumentParser(
        prog="transactions.py", description="Record and manage transactions."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    add = subparsers.add_parser(
        "add", help="add a transaction", parents=[common],
        description="Add an income or expense transaction.",
    )
    add.add_argument("--account", required=True, help="account id or name")
    add.add_argument("--amount", required=True, help="positive amount (magnitude)")
    add.add_argument("--date", required=True, help="date YYYY-MM-DD")
    add.add_argument("--category", help="category id or name")
    add.add_argument("--direction", choices=list(_lib.DIRECTIONS))
    add.add_argument("--payee")
    add.add_argument("--note")
    add.add_argument("--tags", help="comma-separated tags")
    add.set_defaults(func=cmd_add, verb="add")

    transfer = subparsers.add_parser(
        "transfer", help="move money between accounts", parents=[common],
        description="Transfer money between two accounts (excluded from income/expense reports).",
    )
    transfer.add_argument("--from", dest="from_account", required=True, help="source account")
    transfer.add_argument("--to", dest="to_account", required=True, help="destination account")
    transfer.add_argument("--amount", required=True, help="positive amount")
    transfer.add_argument("--date", required=True, help="date YYYY-MM-DD")
    transfer.add_argument("--note")
    transfer.set_defaults(func=cmd_transfer, verb="transfer")

    listing = subparsers.add_parser(
        "list", help="list transactions", parents=[common],
        description="List transactions with optional filters.",
    )
    listing.add_argument("--account")
    listing.add_argument("--from", dest="from_date", help="start date YYYY-MM-DD")
    listing.add_argument("--to", dest="to_date", help="end date YYYY-MM-DD")
    listing.add_argument("--category")
    listing.add_argument("--search", help="substring match on payee, note, or tags")
    listing.add_argument("--limit", type=int, help="maximum rows")
    listing.set_defaults(func=cmd_list, verb="list")

    edit = subparsers.add_parser(
        "edit", help="edit a transaction", parents=[common],
        description="Edit fields on a non-transfer transaction.",
    )
    edit.add_argument("transaction", type=int, help="transaction id")
    edit.add_argument("--account")
    edit.add_argument("--amount")
    edit.add_argument("--date")
    edit.add_argument("--category")
    edit.add_argument("--direction", choices=list(_lib.DIRECTIONS))
    edit.add_argument("--payee")
    edit.add_argument("--note")
    edit.add_argument("--tags")
    edit.set_defaults(func=cmd_edit, verb="edit")

    delete = subparsers.add_parser(
        "delete", help="delete a transaction", parents=[common],
        description="Delete a transaction (deletes both sides of a transfer).",
    )
    delete.add_argument("transaction", type=int, help="transaction id")
    delete.set_defaults(func=cmd_delete, verb="delete")

    return parser


def main() -> None:
    _lib.run_script("transactions", build_parser())


if __name__ == "__main__":
    main()
