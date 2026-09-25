#!/usr/bin/env python3
"""Set monthly category budgets and check spending against them."""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _lib  # noqa: E402


def cmd_set(conn: sqlite3.Connection, args: argparse.Namespace) -> Dict[str, Any]:
    code = _lib.resolve_currency(conn, args.currency)
    category = _lib.resolve_category(conn, args.category, kind="expense")
    month = _lib.validate_month(args.month)
    amount = _lib.parse_amount(
        args.amount, _lib.currency_exponent(code), allow_negative=False, field="budget amount"
    )
    conn.execute(
        "INSERT INTO budgets (category_id, month, amount_units) VALUES (?, ?, ?) "
        "ON CONFLICT(category_id, month) DO UPDATE SET amount_units = excluded.amount_units",
        (category["id"], month, amount),
    )
    return {
        "category_id": category["id"],
        "category": category["name"],
        "month": month,
        "amount_units": amount,
        "amount": _lib.fmt_amount(amount, code),
    }


def cmd_list(conn: sqlite3.Connection, args: argparse.Namespace) -> List[Dict[str, Any]]:
    code = _lib.resolve_currency(conn, args.currency)
    sql = (
        "SELECT b.*, c.name AS category_name FROM budgets b "
        "JOIN categories c ON c.id = b.category_id"
    )
    params: List[Any] = []
    if args.month:
        sql += " WHERE b.month = ?"
        params.append(_lib.validate_month(args.month))
    sql += " ORDER BY b.month DESC, c.name COLLATE NOCASE"
    rows = conn.execute(sql, params).fetchall()
    return [
        {
            "id": row["id"],
            "month": row["month"],
            "category_id": row["category_id"],
            "category": row["category_name"],
            "amount_units": int(row["amount_units"]),
            "amount": _lib.fmt_amount(row["amount_units"], code),
        }
        for row in rows
    ]


def _spent_units(conn: sqlite3.Connection, category_id: int, month: str) -> int:
    start, end = _lib.month_bounds(month)
    row = conn.execute(
        "SELECT COALESCE(SUM(amount_units), 0) AS total FROM transactions "
        "WHERE category_id = ? AND transfer_group IS NULL AND date >= ? AND date <= ?",
        (category_id, start, end),
    ).fetchone()
    return -int(row["total"])


def cmd_status(conn: sqlite3.Connection, args: argparse.Namespace) -> Dict[str, Any]:
    code = _lib.resolve_currency(conn, args.currency)
    month = _lib.validate_month(args.month) if args.month else _lib.current_month()
    rows = conn.execute(
        "SELECT b.*, c.name AS category_name FROM budgets b "
        "JOIN categories c ON c.id = b.category_id WHERE b.month = ? "
        "ORDER BY c.name COLLATE NOCASE",
        (month,),
    ).fetchall()
    budgets: List[Dict[str, Any]] = []
    total_limit = 0
    total_spent = 0
    for row in rows:
        limit = int(row["amount_units"])
        spent = _spent_units(conn, row["category_id"], month)
        remaining = limit - spent
        pct = round((spent / limit) * 100, 2) if limit else None
        total_limit += limit
        total_spent += spent
        budgets.append(
            {
                "category_id": row["category_id"],
                "category": row["category_name"],
                "limit_units": limit,
                "limit": _lib.fmt_amount(limit, code),
                "spent_units": spent,
                "spent": _lib.fmt_amount(spent, code),
                "remaining_units": remaining,
                "remaining": _lib.fmt_amount(remaining, code),
                "percent_used": pct,
                "over_budget": spent > limit,
            }
        )
    return {
        "month": month,
        "currency": code,
        "budgets": budgets,
        "total_limit_units": total_limit,
        "total_limit": _lib.fmt_amount(total_limit, code),
        "total_spent_units": total_spent,
        "total_spent": _lib.fmt_amount(total_spent, code),
        "total_remaining_units": total_limit - total_spent,
        "total_remaining": _lib.fmt_amount(total_limit - total_spent, code),
    }


def build_parser() -> argparse.ArgumentParser:
    common = _lib.common_parser()
    parser = argparse.ArgumentParser(prog="budgets.py", description="Manage monthly budgets.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    set_cmd = subparsers.add_parser(
        "set", help="set a category budget for a month", parents=[common],
        description="Create or replace an expense budget for a category and month.",
    )
    set_cmd.add_argument("--category", required=True)
    set_cmd.add_argument("--month", required=True, help="month YYYY-MM")
    set_cmd.add_argument("--amount", required=True, help="positive budget limit")
    set_cmd.set_defaults(func=cmd_set, verb="set")

    listing = subparsers.add_parser(
        "list", help="list budgets", parents=[common],
        description="List budgets, optionally for one month.",
    )
    listing.add_argument("--month", help="month YYYY-MM")
    listing.set_defaults(func=cmd_list, verb="list")

    status = subparsers.add_parser(
        "status", help="spending versus budget", parents=[common],
        description="Show budgeted, spent, and remaining amounts for a month.",
    )
    status.add_argument("--month", help="month YYYY-MM (default current month)")
    status.set_defaults(func=cmd_status, verb="status")

    return parser


def main() -> None:
    _lib.run_script("budgets", build_parser())


if __name__ == "__main__":
    main()
