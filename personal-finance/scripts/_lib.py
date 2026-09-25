#!/usr/bin/env python3
"""Shared helpers for the personal-finance skill scripts.

This module is NOT an entry point. Domain scripts import it directly
(``import _lib``) because Python places the script's own directory on
``sys.path[0]``.

Pure standard library, compatible with Python 3.9+. No network access.
"""
from __future__ import annotations

import argparse
import calendar
import json
import os
import sqlite3
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

DEFAULT_DB_DIR = "~/.local/share/personal-finance"
DEFAULT_DB_NAME = "finance.db"
DEFAULT_CURRENCY = "USD"
SCHEMA_VERSION = 1

ACCOUNT_TYPES = ("checking", "savings", "credit", "cash", "investment", "loan")
CATEGORY_KINDS = ("income", "expense")
FREQUENCIES = ("daily", "weekly", "monthly", "yearly")
DIRECTIONS = ("in", "out")
ASSET_ACCOUNT_TYPES = ("checking", "savings", "cash", "investment")
LIABILITY_ACCOUNT_TYPES = ("credit", "loan")

# ISO 4217 minor-unit exponents. Anything not listed defaults to 2.
CURRENCY_EXPONENTS: Dict[str, int] = {
    # zero-decimal currencies
    "BIF": 0, "CLP": 0, "DJF": 0, "GNF": 0, "ISK": 0, "JPY": 0,
    "KMF": 0, "KRW": 0, "PYG": 0, "RWF": 0, "UGX": 0, "VND": 0,
    "VUV": 0, "XAF": 0, "XOF": 0, "XPF": 0,
    # three-decimal currencies
    "BHD": 3, "IQD": 3, "JOD": 3, "KWD": 3, "LYD": 3, "OMR": 3, "TND": 3,
}

DEFAULT_CATEGORIES: Tuple[Tuple[str, str], ...] = (
    ("Groceries", "expense"),
    ("Dining", "expense"),
    ("Rent", "expense"),
    ("Utilities", "expense"),
    ("Transport", "expense"),
    ("Health", "expense"),
    ("Entertainment", "expense"),
    ("Shopping", "expense"),
    ("Subscriptions", "expense"),
    ("Travel", "expense"),
    ("Fees", "expense"),
    ("Taxes", "expense"),
    ("Other", "expense"),
    ("Salary", "income"),
    ("Freelance", "income"),
    ("Interest", "income"),
    ("Gifts", "income"),
    ("Other Income", "income"),
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS accounts (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    name                  TEXT NOT NULL UNIQUE,
    type                  TEXT NOT NULL CHECK (type IN
                              ('checking','savings','credit','cash','investment','loan')),
    institution           TEXT,
    opening_balance_units INTEGER NOT NULL DEFAULT 0,
    archived              INTEGER NOT NULL DEFAULT 0,
    created_at            TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS categories (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    kind       TEXT NOT NULL CHECK (kind IN ('income','expense')),
    parent_id  INTEGER REFERENCES categories(id),
    archived   INTEGER NOT NULL DEFAULT 0,
    UNIQUE (name, kind)
);

CREATE TABLE IF NOT EXISTS transactions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id    INTEGER NOT NULL REFERENCES accounts(id),
    date          TEXT NOT NULL,
    amount_units  INTEGER NOT NULL,
    category_id   INTEGER REFERENCES categories(id),
    payee         TEXT,
    note          TEXT,
    tags          TEXT,
    transfer_group TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_transactions_account ON transactions(account_id);
CREATE INDEX IF NOT EXISTS idx_transactions_category ON transactions(category_id);
CREATE INDEX IF NOT EXISTS idx_transactions_transfer_group ON transactions(transfer_group);

CREATE TABLE IF NOT EXISTS budgets (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id  INTEGER NOT NULL REFERENCES categories(id),
    month        TEXT NOT NULL,
    amount_units INTEGER NOT NULL,
    UNIQUE (category_id, month)
);

CREATE TABLE IF NOT EXISTS recurring (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    account_id   INTEGER NOT NULL REFERENCES accounts(id),
    amount_units INTEGER NOT NULL,
    category_id  INTEGER REFERENCES categories(id),
    frequency    TEXT NOT NULL CHECK (frequency IN ('daily','weekly','monthly','yearly')),
    interval     INTEGER NOT NULL DEFAULT 1,
    next_due     TEXT NOT NULL,
    end_date     TEXT,
    note         TEXT,
    active       INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS recurring_log (
    recurring_id   INTEGER NOT NULL REFERENCES recurring(id),
    due_date       TEXT NOT NULL,
    transaction_id INTEGER REFERENCES transactions(id),
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (recurring_id, due_date)
);
"""


class CommandError(Exception):
    """A user-facing error that is rendered as a JSON error envelope."""

    def __init__(self, code: str, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.exit_code = exit_code


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def resolve_db_path(cli_value: Optional[str] = None) -> Path:
    if cli_value:
        raw = cli_value
    elif os.environ.get("PERSONAL_FINANCE_DB"):
        raw = os.environ["PERSONAL_FINANCE_DB"]
    else:
        raw = os.path.join(DEFAULT_DB_DIR, DEFAULT_DB_NAME)
    return Path(os.path.expanduser(raw))


def connect(db_path: Path) -> sqlite3.Connection:
    db_path = Path(os.path.expanduser(str(db_path)))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.execute("PRAGMA user_version = %d" % SCHEMA_VERSION)
    conn.commit()


def seed_defaults(conn: sqlite3.Connection) -> int:
    before = conn.execute("SELECT COUNT(*) AS n FROM categories").fetchone()["n"]
    for name, kind in DEFAULT_CATEGORIES:
        conn.execute(
            "INSERT OR IGNORE INTO categories (name, kind) VALUES (?, ?)",
            (name, kind),
        )
    after = conn.execute("SELECT COUNT(*) AS n FROM categories").fetchone()["n"]
    conn.commit()
    return after - before


def get_meta(conn: sqlite3.Connection, key: str, default: Optional[str] = None) -> Optional[str]:
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    except sqlite3.OperationalError:
        return default
    return row["value"] if row else default


def set_meta(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )


# ---------------------------------------------------------------------------
# Currency helpers
# ---------------------------------------------------------------------------

def currency_exponent(code: str) -> int:
    return CURRENCY_EXPONENTS.get((code or "").strip().upper(), 2)


def validate_currency_code(code: str) -> str:
    cleaned = (code or "").strip().upper()
    if not cleaned:
        raise CommandError("invalid_currency", "currency code cannot be empty")
    if not cleaned.isalpha() or not (2 <= len(cleaned) <= 8):
        raise CommandError(
            "invalid_currency",
            "currency must be 2-8 letters (for example IDR, EUR, JPY, NGN)",
        )
    return cleaned


def resolve_currency(
    conn: sqlite3.Connection,
    cli_value: Optional[str] = None,
    default: str = DEFAULT_CURRENCY,
) -> str:
    if cli_value:
        return validate_currency_code(cli_value)
    env = os.environ.get("PERSONAL_FINANCE_CURRENCY")
    if env:
        return validate_currency_code(env)
    stored = get_meta(conn, "currency")
    if stored:
        return validate_currency_code(stored)
    return default


def parse_amount(
    text: Any,
    exponent: int = 2,
    allow_negative: bool = True,
    field: str = "amount",
) -> int:
    """Parse a decimal string into integer minor units."""
    if text is None or (isinstance(text, str) and not text.strip()):
        raise CommandError("invalid_amount", "%s is required" % field)
    if isinstance(text, bool):
        raise CommandError("invalid_amount", "%s must be a number, got a boolean" % field)
    raw = str(text).strip()
    cleaned = raw.replace(",", "").replace("_", "").replace(" ", "")
    try:
        value = Decimal(cleaned)
    except (InvalidOperation, ValueError):
        raise CommandError("invalid_amount", "invalid %s: %r" % (field, raw))
    if not value.is_finite():
        raise CommandError("invalid_amount", "invalid %s: %r" % (field, raw))
    if value < 0 and not allow_negative:
        raise CommandError(
            "invalid_amount",
            "%s must be a positive magnitude; use --direction to say whether money is going in or out"
            % field,
        )
    scale = Decimal(10) ** int(exponent)
    scaled = value * scale
    if scaled != scaled.to_integral_value():
        raise CommandError(
            "invalid_amount",
            "%s %r has more decimal places than %s allows (%d)"
            % (field, raw, "the ledger currency", exponent),
        )
    return int(scaled)


def fmt_amount(units: int, code: str) -> str:
    exponent = currency_exponent(code)
    value = Decimal(abs(int(units))).scaleb(-exponent)
    grouped = format(value, ",.%df" % exponent)
    sign = "-" if int(units) < 0 else ""
    return "%s %s%s" % (code, sign, grouped)


def money(units: int, code: str) -> Dict[str, Any]:
    return {
        "units": int(units),
        "currency": code,
        "formatted": fmt_amount(units, code),
    }


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def today_str() -> str:
    return date.today().isoformat()


def current_month() -> str:
    return date.today().strftime("%Y-%m")


def validate_date(value: str, field: str = "date") -> str:
    text = (value or "").strip()
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        raise CommandError("invalid_date", "invalid %s %r, expected YYYY-MM-DD" % (field, value))


def validate_month(value: str, field: str = "month") -> str:
    text = (value or "").strip()
    try:
        parsed = datetime.strptime(text, "%Y-%m")
    except ValueError:
        raise CommandError("invalid_date", "invalid %s %r, expected YYYY-MM" % (field, value))
    return parsed.strftime("%Y-%m")


def month_bounds(month: str) -> Tuple[str, str]:
    month = validate_month(month)
    year, mon = (int(part) for part in month.split("-"))
    last_day = calendar.monthrange(year, mon)[1]
    return "%04d-%02d-01" % (year, mon), "%04d-%02d-%02d" % (year, mon, last_day)


def add_months(value: date, months: int) -> date:
    total = value.year * 12 + (value.month - 1) + months
    year, month_index = divmod(total, 12)
    month = month_index + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def advance(value: date, frequency: str, interval: int) -> date:
    interval = max(1, int(interval))
    if frequency == "daily":
        return value + timedelta(days=interval)
    if frequency == "weekly":
        return value + timedelta(weeks=interval)
    if frequency == "monthly":
        return add_months(value, interval)
    if frequency == "yearly":
        return add_months(value, 12 * interval)
    raise CommandError("invalid_frequency", "unsupported frequency %r" % frequency)


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------

def resolve_account(
    conn: sqlite3.Connection,
    ident: Any,
    field: str = "account",
    allow_archived: bool = False,
) -> sqlite3.Row:
    if ident is None or not str(ident).strip():
        raise CommandError("not_found", "%s is required" % field)
    text = str(ident).strip()
    row = None
    if text.isdigit():
        row = conn.execute("SELECT * FROM accounts WHERE id = ?", (int(text),)).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT * FROM accounts WHERE name = ? COLLATE NOCASE", (text,)
        ).fetchone()
    if row is None:
        raise CommandError("not_found", "no %s matches %r" % (field, text))
    if row["archived"] and not allow_archived:
        raise CommandError("not_found", "%s %r is archived" % (field, row["name"]))
    return row


def resolve_category(
    conn: sqlite3.Connection,
    ident: Any,
    kind: Optional[str] = None,
    field: str = "category",
    allow_archived: bool = False,
) -> sqlite3.Row:
    if ident is None or not str(ident).strip():
        raise CommandError("not_found", "%s is required" % field)
    text = str(ident).strip()
    row = None
    if text.isdigit():
        row = conn.execute("SELECT * FROM categories WHERE id = ?", (int(text),)).fetchone()
    if row is None and kind:
        row = conn.execute(
            "SELECT * FROM categories WHERE name = ? COLLATE NOCASE AND kind = ?",
            (text, kind),
        ).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT * FROM categories WHERE name = ? COLLATE NOCASE", (text,)
        ).fetchone()
    if row is None:
        raise CommandError("not_found", "no %s matches %r" % (field, text))
    if kind and row["kind"] != kind:
        raise CommandError(
            "invalid_category",
            "category %r is %s, expected %s" % (row["name"], row["kind"], kind),
        )
    if row["archived"] and not allow_archived:
        raise CommandError("not_found", "%s %r is archived" % (field, row["name"]))
    return row


def get_or_create_category(conn: sqlite3.Connection, name: str, kind: str) -> sqlite3.Row:
    name = (name or "").strip()
    if not name:
        raise CommandError("invalid_category", "category name cannot be empty")
    if kind not in CATEGORY_KINDS:
        raise CommandError("invalid_category", "category kind must be income or expense")
    row = conn.execute(
        "SELECT * FROM categories WHERE name = ? COLLATE NOCASE AND kind = ?",
        (name, kind),
    ).fetchone()
    if row is None:
        cur = conn.execute(
            "INSERT INTO categories (name, kind) VALUES (?, ?)", (name, kind)
        )
        row = conn.execute(
            "SELECT * FROM categories WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
    return row


def category_row_to_dict(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    return {"id": row["id"], "name": row["name"], "kind": row["kind"]}


def account_row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "type": row["type"],
        "institution": row["institution"],
        "archived": bool(row["archived"]),
    }


def apply_direction(magnitude: int, direction: str) -> int:
    if direction == "out":
        return -abs(magnitude)
    if direction == "in":
        return abs(magnitude)
    raise CommandError("invalid_direction", "direction must be 'in' or 'out'")


def infer_direction(category: Optional[sqlite3.Row], explicit: Optional[str]) -> Optional[str]:
    if explicit:
        if explicit not in DIRECTIONS:
            raise CommandError("invalid_direction", "direction must be 'in' or 'out'")
        return explicit
    if category is not None:
        return "out" if category["kind"] == "expense" else "in"
    return None


# ---------------------------------------------------------------------------
# CLI plumbing
# ---------------------------------------------------------------------------

def common_parser() -> argparse.ArgumentParser:
    """Shared flags accepted by every domain script."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--db", metavar="PATH", help="SQLite database path")
    parser.add_argument(
        "--format", choices=("json", "table"), default="json", help="output format"
    )
    parser.add_argument(
        "--currency", metavar="CODE", help="override the ledger currency for this call"
    )
    parser.add_argument(
        "--quiet", action="store_true", help="suppress success output (errors still print)"
    )
    return parser


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def _render_rows(rows: Sequence[Dict[str, Any]]) -> str:
    if not rows:
        return "(none)"
    headers: List[str] = []
    for row in rows:
        for key in row.keys():
            if key not in headers:
                headers.append(key)
    cells = [[_cell(row.get(h)) for h in headers] for row in rows]
    widths = [len(h) for h in headers]
    for row in cells:
        for idx, value in enumerate(row):
            widths[idx] = max(widths[idx], len(value))
    lines = []
    header_line = "  ".join(h.ljust(widths[idx]) for idx, h in enumerate(headers))
    lines.append(header_line)
    lines.append("  ".join("-" * widths[idx] for idx in range(len(headers))))
    for row in cells:
        lines.append("  ".join(row[idx].ljust(widths[idx]) for idx in range(len(headers))))
    return "\n".join(lines)


def render_table(data: Any) -> str:
    if isinstance(data, list):
        return _render_rows(data)
    if isinstance(data, dict):
        lines: List[str] = []
        for key, value in data.items():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                nested = _render_rows(value).splitlines()
                indented = "\n".join("    " + line for line in nested)
                lines.append("%s:" % key)
                lines.append(indented)
            else:
                lines.append("%s: %s" % (key, _cell(value)))
        return "\n".join(lines) if lines else "(empty)"
    return _cell(data)


def emit(command: str, data: Any, fmt: str = "json", quiet: bool = False) -> None:
    if quiet:
        return
    if fmt == "table":
        sys.stdout.write(render_table(data) + "\n")
    else:
        sys.stdout.write(
            json.dumps(
                {"ok": True, "command": command, "data": data},
                indent=2,
                ensure_ascii=False,
                default=str,
            )
            + "\n"
        )


def fail(code: str, message: str, exit_code: int = 1) -> None:
    sys.stdout.write(
        json.dumps(
            {"ok": False, "error": {"code": code, "message": message}},
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    )
    sys.exit(exit_code)


def run_script(domain: str, parser: argparse.ArgumentParser) -> None:
    args = parser.parse_args()
    fmt = getattr(args, "format", "json")
    quiet = getattr(args, "quiet", False)
    verb = getattr(args, "verb", domain)
    handler = getattr(args, "func", None)
    if handler is None:
        parser.print_help(sys.stderr)
        sys.exit(2)
    try:
        conn = connect(resolve_db_path(getattr(args, "db", None)))
        try:
            migrate(conn)
            data = handler(conn, args)
            conn.commit()
        finally:
            conn.close()
        emit("%s.%s" % (domain, verb), data, fmt, quiet)
    except CommandError as exc:
        fail(exc.code, exc.message, exc.exit_code)
    except sqlite3.IntegrityError as exc:
        fail("conflict", str(exc))
    except sqlite3.Error as exc:
        fail("db_error", str(exc))
    except BrokenPipeError:
        sys.exit(0)


def add_subparser(subparsers: Any, name: str, help_text: str, parents: Sequence[Any]) -> Any:
    parser = subparsers.add_parser(name, help=help_text, description=help_text, parents=list(parents))
    parser.set_defaults(verb=name)
    return parser
