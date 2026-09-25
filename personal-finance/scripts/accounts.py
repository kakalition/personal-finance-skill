#!/usr/bin/env python3
"""Manage ledger accounts (checking, savings, credit, cash, investment, loan)."""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _lib  # noqa: E402


def _balance_units(conn: sqlite3.Connection, account_id: int, as_of: str) -> int:
    row = conn.execute(
        "SELECT a.opening_balance_units + COALESCE("
        "  (SELECT SUM(t.amount_units) FROM transactions t "
        "   WHERE t.account_id = a.id AND t.date <= ?), 0) AS balance_units "
        "FROM accounts a WHERE a.id = ?",
        (as_of, account_id),
    ).fetchone()
    return int(row["balance_units"]) if row else 0


def _account_dict(conn: sqlite3.Connection, row: sqlite3.Row, code: str, as_of: str) -> Dict[str, Any]:
    units = _balance_units(conn, row["id"], as_of)
    return {
        "id": row["id"],
        "name": row["name"],
        "type": row["type"],
        "institution": row["institution"],
        "archived": bool(row["archived"]),
        "opening_balance": _lib.fmt_amount(row["opening_balance_units"], code),
        "opening_balance_units": int(row["opening_balance_units"]),
        "balance": _lib.fmt_amount(units, code),
        "balance_units": units,
        "liability": row["type"] in _lib.LIABILITY_ACCOUNT_TYPES,
    }


def cmd_add(conn: sqlite3.Connection, args: argparse.Namespace) -> Dict[str, Any]:
    code = _lib.resolve_currency(conn, args.currency)
    name = (args.name or "").strip()
    if not name:
        raise _lib.CommandError("invalid_input", "account name cannot be empty")
    if args.type not in _lib.ACCOUNT_TYPES:
        raise _lib.CommandError(
            "invalid_input", "account type must be one of: %s" % ", ".join(_lib.ACCOUNT_TYPES)
        )
    existing = conn.execute(
        "SELECT id FROM accounts WHERE name = ? COLLATE NOCASE", (name,)
    ).fetchone()
    if existing:
        raise _lib.CommandError("conflict", "an account named %r already exists" % name)
    opening = _lib.parse_amount(
        args.opening_balance, _lib.currency_exponent(code), field="opening balance"
    )
    cur = conn.execute(
        "INSERT INTO accounts (name, type, institution, opening_balance_units) VALUES (?, ?, ?, ?)",
        (name, args.type, args.institution, opening),
    )
    row = conn.execute("SELECT * FROM accounts WHERE id = ?", (cur.lastrowid,)).fetchone()
    data = _account_dict(conn, row, code, _lib.today_str())
    return data


def cmd_list(conn: sqlite3.Connection, args: argparse.Namespace) -> List[Dict[str, Any]]:
    code = _lib.resolve_currency(conn, args.currency)
    sql = "SELECT * FROM accounts"
    params: List[Any] = []
    if not args.include_archived:
        sql += " WHERE archived = 0"
    sql += " ORDER BY name COLLATE NOCASE"
    rows = conn.execute(sql, params).fetchall()
    as_of = _lib.today_str()
    return [_account_dict(conn, row, code, as_of) for row in rows]


def cmd_show(conn: sqlite3.Connection, args: argparse.Namespace) -> Dict[str, Any]:
    code = _lib.resolve_currency(conn, args.currency)
    row = _lib.resolve_account(conn, args.account, allow_archived=True)
    data = _account_dict(conn, row, code, _lib.today_str())
    data["transaction_count"] = conn.execute(
        "SELECT COUNT(*) AS n FROM transactions WHERE account_id = ?", (row["id"],)
    ).fetchone()["n"]
    return data


def cmd_rename(conn: sqlite3.Connection, args: argparse.Namespace) -> Dict[str, Any]:
    code = _lib.resolve_currency(conn, args.currency)
    row = _lib.resolve_account(conn, args.account, allow_archived=True)
    name = (args.name or "").strip()
    if not name:
        raise _lib.CommandError("invalid_input", "new account name cannot be empty")
    clash = conn.execute(
        "SELECT id FROM accounts WHERE name = ? COLLATE NOCASE AND id != ?", (name, row["id"])
    ).fetchone()
    if clash:
        raise _lib.CommandError("conflict", "an account named %r already exists" % name)
    conn.execute("UPDATE accounts SET name = ? WHERE id = ?", (name, row["id"]))
    updated = conn.execute("SELECT * FROM accounts WHERE id = ?", (row["id"],)).fetchone()
    return _account_dict(conn, updated, code, _lib.today_str())


def cmd_archive(conn: sqlite3.Connection, args: argparse.Namespace) -> Dict[str, Any]:
    code = _lib.resolve_currency(conn, args.currency)
    row = _lib.resolve_account(conn, args.account, allow_archived=True)
    conn.execute("UPDATE accounts SET archived = 1 WHERE id = ?", (row["id"],))
    updated = conn.execute("SELECT * FROM accounts WHERE id = ?", (row["id"],)).fetchone()
    data = _account_dict(conn, updated, code, _lib.today_str())
    data["note"] = "Archived accounts are hidden from lists but keep their transactions."
    return data


def cmd_balance(conn: sqlite3.Connection, args: argparse.Namespace) -> Dict[str, Any]:
    code = _lib.resolve_currency(conn, args.currency)
    as_of = _lib.validate_date(args.as_of) if args.as_of else _lib.today_str()
    sql = "SELECT * FROM accounts"
    if not args.include_archived:
        sql += " WHERE archived = 0"
    sql += " ORDER BY name COLLATE NOCASE"
    rows = conn.execute(sql).fetchall()
    accounts = []
    total = 0
    assets = 0
    liabilities = 0
    for row in rows:
        units = _balance_units(conn, row["id"], as_of)
        total += units
        if row["type"] in _lib.LIABILITY_ACCOUNT_TYPES:
            liabilities += units
        else:
            assets += units
        accounts.append(
            {
                "id": row["id"],
                "name": row["name"],
                "type": row["type"],
                "balance_units": units,
                "balance": _lib.fmt_amount(units, code),
            }
        )
    return {
        "as_of": as_of,
        "currency": code,
        "accounts": accounts,
        "assets_units": assets,
        "assets": _lib.fmt_amount(assets, code),
        "liabilities_units": liabilities,
        "liabilities": _lib.fmt_amount(liabilities, code),
        "total_units": total,
        "total": _lib.fmt_amount(total, code),
    }


def build_parser() -> argparse.ArgumentParser:
    common = _lib.common_parser()
    parser = argparse.ArgumentParser(
        prog="accounts.py", description="Manage personal-finance accounts."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    add = subparsers.add_parser(
        "add", help="create an account", parents=[common],
        description="Create an account with an optional opening balance.",
    )
    add.add_argument("--name", required=True, help="unique account name")
    add.add_argument("--type", required=True, choices=list(_lib.ACCOUNT_TYPES))
    add.add_argument("--institution", help="bank or provider name")
    add.add_argument(
        "--opening-balance", default="0",
        help="signed opening balance in the ledger currency (default 0)",
    )
    add.set_defaults(func=cmd_add, verb="add")

    listing = subparsers.add_parser(
        "list", help="list accounts", parents=[common],
        description="List accounts (active by default).",
    )
    listing.add_argument("--include-archived", action="store_true")
    listing.set_defaults(func=cmd_list, verb="list")

    show = subparsers.add_parser(
        "show", help="show one account", parents=[common],
        description="Show an account by id or name, with its current balance.",
    )
    show.add_argument("account", help="account id or name")
    show.set_defaults(func=cmd_show, verb="show")

    rename = subparsers.add_parser(
        "rename", help="rename an account", parents=[common],
        description="Rename an account by id or name.",
    )
    rename.add_argument("account", help="account id or name")
    rename.add_argument("--name", required=True, help="new account name")
    rename.set_defaults(func=cmd_rename, verb="rename")

    archive = subparsers.add_parser(
        "archive", help="archive an account", parents=[common],
        description="Archive an account (keeps its transactions).",
    )
    archive.add_argument("account", help="account id or name")
    archive.set_defaults(func=cmd_archive, verb="archive")

    balance = subparsers.add_parser(
        "balance", help="show balances", parents=[common],
        description="Show per-account balances and totals as of a date.",
    )
    balance.add_argument("--as-of", help="date YYYY-MM-DD (default today)")
    balance.add_argument("--include-archived", action="store_true")
    balance.set_defaults(func=cmd_balance, verb="balance")

    return parser


def main() -> None:
    _lib.run_script("accounts", build_parser())


if __name__ == "__main__":
    main()
