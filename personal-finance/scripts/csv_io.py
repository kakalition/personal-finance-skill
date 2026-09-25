#!/usr/bin/env python3
"""Import and export transactions as CSV.

Exported amounts are signed decimal strings (negative means money out). Import
treats CSV amounts as signed for the same reason, so re-importing an export
round-trips without flipping directions.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sqlite3
import sys
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _lib  # noqa: E402

EXPORT_COLUMNS = ("date", "account", "amount", "category", "payee", "note", "tags", "transfer_group")


def _amount_str(units: int, code: str) -> str:
    exponent = _lib.currency_exponent(code)
    value = Decimal(abs(int(units))).scaleb(-exponent)
    text = format(value, "f")
    if exponent:
        whole, _, frac = text.partition(".")
        frac = (frac + "0" * exponent)[:exponent]
        text = "%s.%s" % (whole, frac)
    return ("-" if units < 0 else "") + text


def _row_to_export(row: sqlite3.Row, code: str) -> Dict[str, str]:
    return {
        "date": row["date"],
        "account": row["account_name"],
        "amount": _amount_str(int(row["amount_units"]), code),
        "category": row["category_name"] or "",
        "payee": row["payee"] or "",
        "note": row["note"] or "",
        "tags": row["tags"] or "",
        "transfer_group": row["transfer_group"] or "",
    }


def cmd_export(conn: sqlite3.Connection, args: argparse.Namespace) -> Dict[str, Any]:
    code = _lib.resolve_currency(conn, args.currency)
    sql = (
        "SELECT t.*, a.name AS account_name, c.name AS category_name "
        "FROM transactions t JOIN accounts a ON a.id = t.account_id "
        "LEFT JOIN categories c ON c.id = t.category_id"
    )
    clauses: List[str] = []
    params: List[Any] = []
    if args.from_date:
        clauses.append("t.date >= ?")
        params.append(_lib.validate_date(args.from_date, "--from"))
    if args.to_date:
        clauses.append("t.date <= ?")
        params.append(_lib.validate_date(args.to_date, "--to"))
    if args.account:
        clauses.append("t.account_id = ?")
        params.append(_lib.resolve_account(conn, args.account)["id"])
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY t.date, t.id"
    rows = conn.execute(sql, params).fetchall()

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(EXPORT_COLUMNS))
    writer.writeheader()
    for row in rows:
        writer.writerow(_row_to_export(row, code))
    content = buffer.getvalue()

    result: Dict[str, Any] = {
        "rows": len(rows),
        "columns": list(EXPORT_COLUMNS),
        "currency": code,
    }
    if args.output:
        output_path = os.path.expanduser(args.output)
        parent = os.path.dirname(os.path.abspath(output_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(output_path, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
        result["path"] = output_path
    else:
        result["csv"] = content
    return result


def _load_mapping(args: argparse.Namespace) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    if args.mapping:
        try:
            parsed = json.loads(args.mapping)
        except json.JSONDecodeError as exc:
            raise _lib.CommandError("invalid_input", "--mapping is not valid JSON: %s" % exc)
        if not isinstance(parsed, dict):
            raise _lib.CommandError("invalid_input", "--mapping must be a JSON object")
        mapping = {str(key): str(value) for key, value in parsed.items()}
    if args.date_column:
        mapping["date"] = args.date_column
    if args.amount_column:
        mapping["amount"] = args.amount_column
    mapping.setdefault("date", "date")
    mapping.setdefault("amount", "amount")
    mapping.setdefault("payee", "payee")
    mapping.setdefault("note", "note")
    mapping.setdefault("category", "category")
    mapping.setdefault("tags", "tags")
    return mapping


def _parse_due_date(value: str, date_format: Optional[str], row_number: int) -> str:
    text = (value or "").strip()
    if date_format:
        try:
            return datetime.strptime(text, date_format).date().isoformat()
        except ValueError:
            raise _lib.CommandError(
                "invalid_date",
                "row %d: date %r does not match format %r" % (row_number, value, date_format),
            )
    try:
        return _lib.validate_date(text)
    except _lib.CommandError:
        raise _lib.CommandError(
            "invalid_date", "row %d: date %r is not YYYY-MM-DD" % (row_number, value)
        )


def cmd_import(conn: sqlite3.Connection, args: argparse.Namespace) -> Dict[str, Any]:
    code = _lib.resolve_currency(conn, args.currency)
    account = _lib.resolve_account(conn, args.account)
    default_category = None
    if args.category:
        default_category = _lib.resolve_category(conn, args.category, allow_archived=True)
    mapping = _load_mapping(args)
    path = os.path.expanduser(args.file)
    if not os.path.isfile(path):
        raise _lib.CommandError("not_found", "CSV file not found: %s" % path)
    exponent = _lib.currency_exponent(code)

    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=args.delimiter)
        if reader.fieldnames is None:
            raise _lib.CommandError("invalid_input", "CSV file has no header row")
        headers = [name.strip() for name in reader.fieldnames]
        for required in ("date", "amount"):
            column = mapping[required]
            if column not in headers:
                raise _lib.CommandError(
                    "invalid_input",
                    "column %r not found in CSV (available: %s)" % (column, ", ".join(headers)),
                )
        raw_rows = list(reader)

    parsed: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    for index, raw in enumerate(raw_rows, start=2):
        if not any((value or "").strip() for value in raw.values()):
            continue
        try:
            due = _parse_due_date(raw.get(mapping["date"], ""), args.date_format, index)
            units = _lib.parse_amount(
                raw.get(mapping["amount"], ""), exponent, allow_negative=True, field="amount"
            )
            category_name = None
            if mapping.get("category"):
                category_name = (raw.get(mapping["category"]) or "").strip() or None
            parsed.append(
                {
                    "date": due,
                    "units": units,
                    "payee": (raw.get(mapping.get("payee", ""), "") or "").strip() or None,
                    "note": (raw.get(mapping.get("note", ""), "") or "").strip() or None,
                    "tags": (raw.get(mapping.get("tags", ""), "") or "").strip() or None,
                    "category_name": category_name,
                }
            )
        except _lib.CommandError as exc:
            errors.append({"row": index, "error": exc.message})

    if errors and not args.force:
        first = errors[0]
        raise _lib.CommandError(
            "invalid_rows",
            "%d invalid row(s); nothing imported. First: row %s: %s"
            % (len(errors), first["row"], first["error"]),
        )

    imported: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for item in parsed:
        payee = item["payee"]
        duplicate = conn.execute(
            "SELECT id FROM transactions WHERE account_id = ? AND date = ? AND amount_units = ? "
            "AND COALESCE(payee, '') = COALESCE(?, '')",
            (account["id"], item["date"], item["units"], payee),
        ).fetchone()
        if duplicate and not args.force:
            skipped.append({"date": item["date"], "amount_units": item["units"], "payee": payee})
            continue
        category_id = None
        if item["category_name"]:
            kind = "expense" if item["units"] < 0 else "income"
            category_id = _lib.get_or_create_category(conn, item["category_name"], kind)["id"]
        elif default_category is not None:
            category_id = default_category["id"]
        if args.dry_run:
            imported.append(
                {
                    "date": item["date"],
                    "amount_units": item["units"],
                    "payee": payee,
                    "category_id": category_id,
                    "dry_run": True,
                }
            )
            continue
        tx_id = conn.execute(
            "INSERT INTO transactions (account_id, date, amount_units, category_id, payee, note, tags) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (account["id"], item["date"], item["units"], category_id, payee, item["note"], item["tags"]),
        ).lastrowid
        imported.append(
            {
                "id": tx_id,
                "date": item["date"],
                "amount_units": item["units"],
                "amount": _lib.fmt_amount(item["units"], code),
                "payee": payee,
            }
        )

    return {
        "file": path,
        "account": account["name"],
        "dry_run": bool(args.dry_run),
        "force": bool(args.force),
        "currency": code,
        "imported_count": len(imported),
        "imported": imported,
        "skipped_count": len(skipped),
        "skipped": skipped,
        "error_count": len(errors),
        "errors": errors,
    }


def build_parser() -> argparse.ArgumentParser:
    common = _lib.common_parser()
    parser = argparse.ArgumentParser(
        prog="csv_io.py", description="Import and export transactions as CSV."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    export = subparsers.add_parser(
        "export", help="export transactions to CSV", parents=[common],
        description="Export transactions to a CSV file or stdout.",
    )
    export.add_argument("--from", dest="from_date", help="start date YYYY-MM-DD")
    export.add_argument("--to", dest="to_date", help="end date YYYY-MM-DD")
    export.add_argument("--account")
    export.add_argument("--output", help="write to this path (otherwise returns CSV text)")
    export.set_defaults(func=cmd_export, verb="export")

    import_cmd = subparsers.add_parser(
        "import", help="import transactions from CSV", parents=[common],
        description="Import transactions from a CSV file, skipping duplicates by default.",
    )
    import_cmd.add_argument("file", help="path to the CSV file")
    import_cmd.add_argument("--account", required=True)
    import_cmd.add_argument("--date-column")
    import_cmd.add_argument("--amount-column")
    import_cmd.add_argument("--date-format", help="strptime format, e.g. %%d/%%m/%%Y")
    import_cmd.add_argument("--delimiter", default=",")
    import_cmd.add_argument("--mapping", help="JSON column map, e.g. {\"date\":\"Date\"}")
    import_cmd.add_argument("--category", help="default category for rows without one")
    import_cmd.add_argument("--dry-run", action="store_true")
    import_cmd.add_argument("--force", action="store_true", help="import duplicates and skip invalid rows")
    import_cmd.set_defaults(func=cmd_import, verb="import")

    return parser


def main() -> None:
    _lib.run_script("csv_io", build_parser())


if __name__ == "__main__":
    main()
