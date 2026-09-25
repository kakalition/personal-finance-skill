#!/usr/bin/env python3
"""Manage income and expense categories."""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _lib  # noqa: E402


def _category_dict(row: sqlite3.Row, parent_name: Optional[str] = None) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "id": row["id"],
        "name": row["name"],
        "kind": row["kind"],
        "parent_id": row["parent_id"],
        "parent": parent_name,
        "archived": bool(row["archived"]),
    }
    return data


def cmd_add(conn: sqlite3.Connection, args: argparse.Namespace) -> Dict[str, Any]:
    name = (args.name or "").strip()
    if not name:
        raise _lib.CommandError("invalid_input", "category name cannot be empty")
    if args.kind not in _lib.CATEGORY_KINDS:
        raise _lib.CommandError("invalid_input", "category kind must be income or expense")
    parent_id = None
    if args.parent:
        parent = _lib.resolve_category(conn, args.parent)
        if parent["kind"] != args.kind:
            raise _lib.CommandError(
                "invalid_category",
                "parent category %r is %s but the new category is %s"
                % (parent["name"], parent["kind"], args.kind),
            )
        parent_id = parent["id"]
    existing = conn.execute(
        "SELECT id FROM categories WHERE name = ? COLLATE NOCASE AND kind = ?",
        (name, args.kind),
    ).fetchone()
    if existing:
        raise _lib.CommandError(
            "conflict", "a %s category named %r already exists" % (args.kind, name)
        )
    cur = conn.execute(
        "INSERT INTO categories (name, kind, parent_id) VALUES (?, ?, ?)",
        (name, args.kind, parent_id),
    )
    row = conn.execute("SELECT * FROM categories WHERE id = ?", (cur.lastrowid,)).fetchone()
    parent_name = None
    if parent_id is not None:
        parent_name = conn.execute(
            "SELECT name FROM categories WHERE id = ?", (parent_id,)
        ).fetchone()["name"]
    return _category_dict(row, parent_name)


def cmd_list(conn: sqlite3.Connection, args: argparse.Namespace) -> List[Dict[str, Any]]:
    sql = (
        "SELECT c.*, p.name AS parent_name FROM categories c "
        "LEFT JOIN categories p ON p.id = c.parent_id"
    )
    clauses: List[str] = []
    params: List[Any] = []
    if args.kind:
        if args.kind not in _lib.CATEGORY_KINDS:
            raise _lib.CommandError("invalid_input", "category kind must be income or expense")
        clauses.append("c.kind = ?")
        params.append(args.kind)
    if not args.include_archived:
        clauses.append("c.archived = 0")
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY c.kind, c.name COLLATE NOCASE"
    rows = conn.execute(sql, params).fetchall()
    return [_category_dict(row, row["parent_name"]) for row in rows]


def cmd_rename(conn: sqlite3.Connection, args: argparse.Namespace) -> Dict[str, Any]:
    row = _lib.resolve_category(conn, args.category, allow_archived=True)
    name = (args.name or "").strip()
    if not name:
        raise _lib.CommandError("invalid_input", "new category name cannot be empty")
    clash = conn.execute(
        "SELECT id FROM categories WHERE name = ? COLLATE NOCASE AND kind = ? AND id != ?",
        (name, row["kind"], row["id"]),
    ).fetchone()
    if clash:
        raise _lib.CommandError(
            "conflict", "a %s category named %r already exists" % (row["kind"], name)
        )
    conn.execute("UPDATE categories SET name = ? WHERE id = ?", (name, row["id"]))
    updated = conn.execute("SELECT * FROM categories WHERE id = ?", (row["id"],)).fetchone()
    parent_name = None
    if updated["parent_id"] is not None:
        parent_name = conn.execute(
            "SELECT name FROM categories WHERE id = ?", (updated["parent_id"],)
        ).fetchone()["name"]
    return _category_dict(updated, parent_name)


def cmd_archive(conn: sqlite3.Connection, args: argparse.Namespace) -> Dict[str, Any]:
    row = _lib.resolve_category(conn, args.category, allow_archived=True)
    conn.execute("UPDATE categories SET archived = 1 WHERE id = ?", (row["id"],))
    updated = conn.execute("SELECT * FROM categories WHERE id = ?", (row["id"],)).fetchone()
    data = _category_dict(updated, None)
    data["note"] = "Archived categories stay attached to existing transactions."
    return data


def build_parser() -> argparse.ArgumentParser:
    common = _lib.common_parser()
    parser = argparse.ArgumentParser(
        prog="categories.py", description="Manage income and expense categories."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    add = subparsers.add_parser(
        "add", help="create a category", parents=[common],
        description="Create an income or expense category, optionally under a parent.",
    )
    add.add_argument("--name", required=True)
    add.add_argument("--kind", required=True, choices=list(_lib.CATEGORY_KINDS))
    add.add_argument("--parent", help="parent category id or name")
    add.set_defaults(func=cmd_add, verb="add")

    listing = subparsers.add_parser(
        "list", help="list categories", parents=[common],
        description="List categories, optionally filtered by kind.",
    )
    listing.add_argument("--kind", choices=list(_lib.CATEGORY_KINDS))
    listing.add_argument("--include-archived", action="store_true")
    listing.set_defaults(func=cmd_list, verb="list")

    rename = subparsers.add_parser(
        "rename", help="rename a category", parents=[common],
        description="Rename a category by id or name.",
    )
    rename.add_argument("category", help="category id or name")
    rename.add_argument("--name", required=True)
    rename.set_defaults(func=cmd_rename, verb="rename")

    archive = subparsers.add_parser(
        "archive", help="archive a category", parents=[common],
        description="Archive a category (keeps it on existing transactions).",
    )
    archive.add_argument("category", help="category id or name")
    archive.set_defaults(func=cmd_archive, verb="archive")

    return parser


def main() -> None:
    _lib.run_script("categories", build_parser())


if __name__ == "__main__":
    main()
