#!/usr/bin/env python3
"""Read and change ledger settings (currently the single ledger currency)."""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from typing import Any, Dict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _lib  # noqa: E402


def _counts(conn: sqlite3.Connection) -> Dict[str, int]:
    tables = ("accounts", "categories", "transactions", "budgets", "recurring")
    counts = {}
    for table in tables:
        counts[table] = conn.execute("SELECT COUNT(*) AS n FROM %s" % table).fetchone()["n"]
    return counts


def _meta_map(conn: sqlite3.Connection) -> Dict[str, str]:
    return {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM meta")}


def cmd_show(conn: sqlite3.Connection, args: argparse.Namespace) -> Dict[str, Any]:
    code = _lib.resolve_currency(conn, args.currency)
    return {
        "currency": code,
        "currency_exponent": _lib.currency_exponent(code),
        "sample_amount": _lib.fmt_amount(125000, code),
        "db": str(_lib.resolve_db_path(args.db)),
        "schema_version": conn.execute("PRAGMA user_version").fetchone()[0],
        "initialized": _lib.get_meta(conn, "initialized") == "1",
        "counts": _counts(conn),
    }


def cmd_get(conn: sqlite3.Connection, args: argparse.Namespace) -> Dict[str, Any]:
    if not args.key:
        meta = _meta_map(conn)
        return {
            "currency": _lib.resolve_currency(conn, args.currency),
            "db": str(_lib.resolve_db_path(args.db)),
            "schema_version": conn.execute("PRAGMA user_version").fetchone()[0],
            "meta": meta,
        }
    key = args.key.strip()
    if key in ("currency", "ledger_currency"):
        return {"key": "currency", "value": _lib.resolve_currency(conn, args.currency)}
    if key in ("db", "db_path"):
        return {"key": "db", "value": str(_lib.resolve_db_path(args.db))}
    if key in ("schema_version", "user_version"):
        return {"key": "schema_version", "value": conn.execute("PRAGMA user_version").fetchone()[0]}
    value = _lib.get_meta(conn, key)
    if value is None:
        raise _lib.CommandError("not_found", "no setting named %r" % key)
    return {"key": key, "value": value}


def cmd_set(conn: sqlite3.Connection, args: argparse.Namespace) -> Dict[str, Any]:
    if not args.currency:
        raise _lib.CommandError(
            "usage", "set requires --currency CODE (e.g. settings.py set --currency EUR)"
        )
    new_code = _lib.validate_currency_code(args.currency)
    previous = _lib.get_meta(conn, "currency")
    _lib.set_meta(conn, "currency", new_code)
    _lib.set_meta(conn, "initialized", "1")
    result: Dict[str, Any] = {
        "currency": new_code,
        "currency_exponent": _lib.currency_exponent(new_code),
        "previous_currency": previous,
        "changed": previous != new_code,
    }
    if previous and previous.upper() != new_code:
        result["warning"] = "existing amounts are relabeled, not converted"
    return result


def build_parser() -> argparse.ArgumentParser:
    common = _lib.common_parser()
    parser = argparse.ArgumentParser(
        prog="settings.py",
        description="Show or change personal-finance ledger settings.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    show = subparsers.add_parser(
        "show", help="show ledger settings and row counts",
        description="Show ledger currency, exponent, database path, and row counts.",
        parents=[common],
    )
    show.set_defaults(func=cmd_show, verb="show")

    get = subparsers.add_parser(
        "get", help="read one setting or all settings",
        description="Read a single setting value or the whole settings map.",
        parents=[common],
    )
    get.add_argument("--key", help="setting name, e.g. currency")
    get.set_defaults(func=cmd_get, verb="get")

    set_cmd = subparsers.add_parser(
        "set", help="change the ledger currency (relabels, does not convert)",
        description="Change the single ledger currency. Existing amounts are relabeled, not converted.",
        parents=[common],
    )
    set_cmd.set_defaults(func=cmd_set, verb="set")

    return parser


def main() -> None:
    _lib.run_script("settings", build_parser())


if __name__ == "__main__":
    main()
