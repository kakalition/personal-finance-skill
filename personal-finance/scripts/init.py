#!/usr/bin/env python3
"""Initialize the personal-finance ledger.

Creates the schema, seeds default categories, and stores the ledger currency.
Safe to run more than once (idempotent).
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from typing import Any, Dict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _lib  # noqa: E402


def cmd_init(conn: sqlite3.Connection, args: argparse.Namespace) -> Dict[str, Any]:
    already_initialized = _lib.get_meta(conn, "initialized") is not None
    seeded = _lib.seed_defaults(conn)

    if args.currency:
        code = _lib.validate_currency_code(args.currency)
        _lib.set_meta(conn, "currency", code)
    else:
        code = _lib.resolve_currency(conn)

    _lib.set_meta(conn, "initialized", "1")
    db_path = _lib.resolve_db_path(args.db)

    return {
        "initialized": True,
        "already_initialized": already_initialized,
        "currency": code,
        "currency_exponent": _lib.currency_exponent(code),
        "schema_version": _lib.SCHEMA_VERSION,
        "db": str(db_path),
        "categories_seeded": seeded,
        "hint": "Run settings.py show to confirm the ledger currency.",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="init.py",
        description="Initialize the personal-finance ledger (schema, categories, currency).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    init_parser = subparsers.add_parser(
        "init",
        help="create the ledger if needed and set the currency",
        description="Create the schema, seed default categories, and set the ledger currency.",
        parents=[_lib.common_parser()],
    )
    init_parser.set_defaults(func=cmd_init, verb="init")
    return parser


def main() -> None:
    _lib.run_script("init", build_parser())


if __name__ == "__main__":
    main()
