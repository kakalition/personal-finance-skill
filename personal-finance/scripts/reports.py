#!/usr/bin/env python3
"""Reporting: spending summary, category breakdown, cashflow, and net worth."""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import date
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _lib  # noqa: E402


def _sum_income_expense(conn: sqlite3.Connection, start: str, end: str) -> Dict[str, int]:
    row = conn.execute(
        "SELECT "
        "  COALESCE(SUM(CASE WHEN amount_units > 0 THEN amount_units ELSE 0 END), 0) AS income, "
        "  COALESCE(SUM(CASE WHEN amount_units < 0 THEN amount_units ELSE 0 END), 0) AS expense_raw "
        "FROM transactions "
        "WHERE transfer_group IS NULL AND date >= ? AND date <= ?",
        (start, end),
    ).fetchone()
    income = int(row["income"])
    expenses = -int(row["expense_raw"])
    return {"income": income, "expenses": expenses, "net": income - expenses}


def _balance_units(conn: sqlite3.Connection, account_id: int, as_of: str) -> int:
    row = conn.execute(
        "SELECT a.opening_balance_units + COALESCE("
        "  (SELECT SUM(t.amount_units) FROM transactions t "
        "   WHERE t.account_id = a.id AND t.date <= ?), 0) AS balance_units "
        "FROM accounts a WHERE a.id = ?",
        (as_of, account_id),
    ).fetchone()
    return int(row["balance_units"]) if row else 0


def cmd_summary(conn: sqlite3.Connection, args: argparse.Namespace) -> Dict[str, Any]:
    code = _lib.resolve_currency(conn, args.currency)
    month = _lib.validate_month(args.month) if args.month else _lib.current_month()
    start, end = _lib.month_bounds(month)
    totals = _sum_income_expense(conn, start, end)
    tx_count = conn.execute(
        "SELECT COUNT(*) AS n FROM transactions WHERE transfer_group IS NULL AND date >= ? AND date <= ?",
        (start, end),
    ).fetchone()["n"]
    transfer_count = conn.execute(
        "SELECT COUNT(*) AS n FROM transactions WHERE transfer_group IS NOT NULL AND date >= ? AND date <= ?",
        (start, end),
    ).fetchone()["n"]
    return {
        "month": month,
        "currency": code,
        "income_units": totals["income"],
        "income": _lib.fmt_amount(totals["income"], code),
        "expenses_units": totals["expenses"],
        "expenses": _lib.fmt_amount(totals["expenses"], code),
        "net_units": totals["net"],
        "net": _lib.fmt_amount(totals["net"], code),
        "transaction_count": tx_count,
        "transfer_count": transfer_count,
    }


def cmd_categories(conn: sqlite3.Connection, args: argparse.Namespace) -> Dict[str, Any]:
    code = _lib.resolve_currency(conn, args.currency)
    month = _lib.validate_month(args.month) if args.month else _lib.current_month()
    start, end = _lib.month_bounds(month)
    sql = (
        "SELECT c.id AS category_id, c.name AS category_name, c.kind AS kind, "
        "       COALESCE(SUM(t.amount_units), 0) AS total "
        "FROM transactions t LEFT JOIN categories c ON c.id = t.category_id "
        "WHERE t.transfer_group IS NULL AND t.date >= ? AND t.date <= ?"
    )
    params: List[Any] = [start, end]
    if args.kind:
        if args.kind not in _lib.CATEGORY_KINDS:
            raise _lib.CommandError("invalid_input", "category kind must be income or expense")
        sql += " AND c.kind = ?"
        params.append(args.kind)
    sql += " GROUP BY t.category_id ORDER BY ABS(COALESCE(SUM(t.amount_units), 0)) DESC"
    rows = conn.execute(sql, params).fetchall()
    items: List[Dict[str, Any]] = []
    for row in rows:
        raw = int(row["total"])
        magnitude = abs(raw)
        items.append(
            {
                "category_id": row["category_id"],
                "category": row["category_name"] or "Uncategorized",
                "kind": row["kind"] or ("expense" if raw < 0 else "income"),
                "amount_units": magnitude,
                "amount": _lib.fmt_amount(magnitude, code),
            }
        )
    return {"month": month, "currency": code, "categories": items}


def cmd_cashflow(conn: sqlite3.Connection, args: argparse.Namespace) -> Dict[str, Any]:
    code = _lib.resolve_currency(conn, args.currency)
    start = _lib.validate_date(args.from_date, "--from")
    end = _lib.validate_date(args.to_date, "--to")
    if end < start:
        raise _lib.CommandError("invalid_input", "--to must not be before --from")
    rows = conn.execute(
        "SELECT date, amount_units FROM transactions "
        "WHERE transfer_group IS NULL AND date >= ? AND date <= ? ORDER BY date",
        (start, end),
    ).fetchall()
    buckets: Dict[str, Dict[str, int]] = {}
    order: List[str] = []
    for row in rows:
        day = date.fromisoformat(row["date"])
        if args.group_by == "week":
            iso = day.isocalendar()
            key = "%04d-W%02d" % (iso[0], iso[1])
        else:
            key = day.strftime("%Y-%m")
        if key not in buckets:
            buckets[key] = {"income": 0, "expense_raw": 0, "count": 0}
            order.append(key)
        units = int(row["amount_units"])
        if units > 0:
            buckets[key]["income"] += units
        else:
            buckets[key]["expense_raw"] += units
        buckets[key]["count"] += 1
    result: List[Dict[str, Any]] = []
    for key in order:
        income = buckets[key]["income"]
        expenses = -buckets[key]["expense_raw"]
        result.append(
            {
                "period": key,
                "income_units": income,
                "income": _lib.fmt_amount(income, code),
                "expenses_units": expenses,
                "expenses": _lib.fmt_amount(expenses, code),
                "net_units": income - expenses,
                "net": _lib.fmt_amount(income - expenses, code),
                "transaction_count": buckets[key]["count"],
            }
        )
    return {
        "from": start,
        "to": end,
        "group_by": args.group_by,
        "currency": code,
        "periods": result,
    }


def cmd_networth(conn: sqlite3.Connection, args: argparse.Namespace) -> Dict[str, Any]:
    code = _lib.resolve_currency(conn, args.currency)
    as_of = _lib.validate_date(args.as_of, "--as-of") if args.as_of else _lib.today_str()
    rows = conn.execute(
        "SELECT * FROM accounts" + ("" if args.include_archived else " WHERE archived = 0") +
        " ORDER BY name COLLATE NOCASE"
    ).fetchall()
    accounts = []
    assets = 0
    liabilities = 0
    for row in rows:
        units = _balance_units(conn, row["id"], as_of)
        if row["type"] in _lib.LIABILITY_ACCOUNT_TYPES:
            liabilities += units
        else:
            assets += units
        if args.by_account:
            accounts.append(
                {
                    "id": row["id"],
                    "name": row["name"],
                    "type": row["type"],
                    "liability": row["type"] in _lib.LIABILITY_ACCOUNT_TYPES,
                    "balance_units": units,
                    "balance": _lib.fmt_amount(units, code),
                }
            )
    total = assets + liabilities
    data: Dict[str, Any] = {
        "as_of": as_of,
        "currency": code,
        "assets_units": assets,
        "assets": _lib.fmt_amount(assets, code),
        "liabilities_units": liabilities,
        "liabilities": _lib.fmt_amount(liabilities, code),
        "net_worth_units": total,
        "net_worth": _lib.fmt_amount(total, code),
    }
    if args.by_account:
        data["accounts"] = accounts
    return data


def build_parser() -> argparse.ArgumentParser:
    common = _lib.common_parser()
    parser = argparse.ArgumentParser(prog="reports.py", description="Personal-finance reports.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    summary = subparsers.add_parser(
        "summary", help="monthly income/expense summary", parents=[common],
        description="Show income, expenses, and net for a month.",
    )
    summary.add_argument("--month", help="month YYYY-MM (default current month)")
    summary.set_defaults(func=cmd_summary, verb="summary")

    categories = subparsers.add_parser(
        "categories", help="spending by category", parents=[common],
        description="Show totals by category for a month.",
    )
    categories.add_argument("--month", help="month YYYY-MM (default current month)")
    categories.add_argument("--kind", choices=list(_lib.CATEGORY_KINDS))
    categories.set_defaults(func=cmd_categories, verb="categories")

    cashflow = subparsers.add_parser(
        "cashflow", help="income/expense over time", parents=[common],
        description="Show income, expenses, and net over time by month or week.",
    )
    cashflow.add_argument("--from", dest="from_date", required=True, help="start date YYYY-MM-DD")
    cashflow.add_argument("--to", dest="to_date", required=True, help="end date YYYY-MM-DD")
    cashflow.add_argument("--group-by", choices=("month", "week"), default="month")
    cashflow.set_defaults(func=cmd_cashflow, verb="cashflow")

    networth = subparsers.add_parser(
        "networth", help="assets minus liabilities", parents=[common],
        description="Show net worth as of a date, optionally per account.",
    )
    networth.add_argument("--as-of", help="date YYYY-MM-DD (default today)")
    networth.add_argument("--by-account", action="store_true")
    networth.add_argument("--include-archived", action="store_true")
    networth.set_defaults(func=cmd_networth, verb="networth")

    return parser


def main() -> None:
    _lib.run_script("reports", build_parser())


if __name__ == "__main__":
    main()
