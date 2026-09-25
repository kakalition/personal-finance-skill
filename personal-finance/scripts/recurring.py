#!/usr/bin/env python3
"""Manage recurring transactions and emit (never install) scheduling artifacts.

The recurring table is the source of truth for what repeats and when. Hosts only
trigger: the emitted artifacts call back into this script, which stays passive.
``due`` and ``schedule-hint`` are read-only; ``run`` is the only writer and is
idempotent through the ``recurring_log`` table.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _lib  # noqa: E402

SCHEDULE_TARGETS = (
    "generic",
    "nanobot",
    "claude",
    "codex",
    "crontab",
    "systemd",
    "launchd",
)
OS_TARGETS = ("crontab", "systemd", "launchd")
AGENT_TARGETS = ("generic", "nanobot", "claude", "codex")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _recurring_dict(conn: sqlite3.Connection, row: sqlite3.Row, code: str) -> Dict[str, Any]:
    account = conn.execute("SELECT name FROM accounts WHERE id = ?", (row["account_id"],)).fetchone()
    category = None
    if row["category_id"] is not None:
        cat = conn.execute("SELECT name FROM categories WHERE id = ?", (row["category_id"],)).fetchone()
        category = cat["name"] if cat else None
    units = int(row["amount_units"])
    return {
        "id": row["id"],
        "name": row["name"],
        "account_id": row["account_id"],
        "account": account["name"] if account else None,
        "category_id": row["category_id"],
        "category": category,
        "amount_units": units,
        "amount": _lib.fmt_amount(units, code),
        "direction": "out" if units < 0 else "in",
        "frequency": row["frequency"],
        "interval": int(row["interval"]),
        "next_due": row["next_due"],
        "end_date": row["end_date"],
        "note": row["note"],
        "active": bool(row["active"]),
    }


def resolve_recurring(conn: sqlite3.Connection, ident: Any) -> sqlite3.Row:
    text = str(ident).strip()
    row = None
    if text.isdigit():
        row = conn.execute("SELECT * FROM recurring WHERE id = ?", (int(text),)).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT * FROM recurring WHERE name = ? COLLATE NOCASE ORDER BY active DESC, id",
            (text,),
        ).fetchone()
    if row is None:
        raise _lib.CommandError("not_found", "no recurring transaction matches %r" % text)
    return row


def _occurrences(rec: sqlite3.Row, end_bound: date) -> Tuple[List[date], date]:
    """Return due dates from next_due through end_bound, plus the next date after."""
    current = date.fromisoformat(rec["next_due"])
    end_date = date.fromisoformat(rec["end_date"]) if rec["end_date"] else None
    found: List[date] = []
    guard = 0
    while current <= end_bound:
        if end_date and current > end_date:
            break
        found.append(current)
        current = _lib.advance(current, rec["frequency"], rec["interval"])
        guard += 1
        if guard > 20000:
            raise _lib.CommandError(
                "too_many_occurrences",
                "recurring rule %s produced too many occurrences; check --interval" % rec["name"],
            )
    return found, current


# ---------------------------------------------------------------------------
# Verbs
# ---------------------------------------------------------------------------

def cmd_add(conn: sqlite3.Connection, args: argparse.Namespace) -> Dict[str, Any]:
    code = _lib.resolve_currency(conn, args.currency)
    name = (args.name or "").strip()
    if not name:
        raise _lib.CommandError("invalid_input", "recurring name cannot be empty")
    account = _lib.resolve_account(conn, args.account)
    category = _lib.resolve_category(conn, args.category) if args.category else None
    direction = _lib.infer_direction(category, args.direction) or "out"
    magnitude = _lib.parse_amount(args.amount, _lib.currency_exponent(code), allow_negative=False)
    units = _lib.apply_direction(magnitude, direction)
    if args.frequency not in _lib.FREQUENCIES:
        raise _lib.CommandError(
            "invalid_frequency", "frequency must be one of: %s" % ", ".join(_lib.FREQUENCIES)
        )
    interval = args.interval if args.interval is not None else 1
    if interval < 1:
        raise _lib.CommandError("invalid_input", "--interval must be 1 or greater")
    start = _lib.validate_date(args.start, "--start") if args.start else _lib.today_str()
    end = _lib.validate_date(args.end, "--end") if args.end else None
    if end and end < start:
        raise _lib.CommandError("invalid_input", "--end must not be before --start")
    cur = conn.execute(
        "INSERT INTO recurring (name, account_id, amount_units, category_id, frequency, interval, "
        "next_due, end_date, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            name,
            account["id"],
            units,
            category["id"] if category else None,
            args.frequency,
            interval,
            start,
            end,
            args.note,
        ),
    )
    row = conn.execute("SELECT * FROM recurring WHERE id = ?", (cur.lastrowid,)).fetchone()
    data = _recurring_dict(conn, row, code)
    data["hint"] = (
        "This stores the rule. To automate it, run schedule-hint and register the "
        "artifact with your host's scheduler."
    )
    return data


def cmd_list(conn: sqlite3.Connection, args: argparse.Namespace) -> List[Dict[str, Any]]:
    code = _lib.resolve_currency(conn, args.currency)
    sql = "SELECT * FROM recurring"
    if not args.include_inactive:
        sql += " WHERE active = 1"
    sql += " ORDER BY next_due, name COLLATE NOCASE"
    rows = conn.execute(sql).fetchall()
    return [_recurring_dict(conn, row, code) for row in rows]


def cmd_due(conn: sqlite3.Connection, args: argparse.Namespace) -> Dict[str, Any]:
    code = _lib.resolve_currency(conn, args.currency)
    as_of = _lib.validate_date(args.as_of, "--as-of") if args.as_of else _lib.today_str()
    within = args.within if args.within is not None else 14
    if within < 0:
        raise _lib.CommandError("invalid_input", "--within must be zero or greater")
    as_of_date = date.fromisoformat(as_of)
    window_end = as_of_date + timedelta(days=within)
    rows = conn.execute(
        "SELECT * FROM recurring WHERE active = 1 ORDER BY next_due"
    ).fetchall()
    instances: List[Dict[str, Any]] = []
    for rec in rows:
        found, _ = _occurrences(rec, window_end)
        base = _recurring_dict(conn, rec, code)
        for due in found:
            instances.append(
                {
                    "recurring_id": rec["id"],
                    "name": rec["name"],
                    "account": base["account"],
                    "category": base["category"],
                    "due_date": due.isoformat(),
                    "amount_units": base["amount_units"],
                    "amount": base["amount"],
                    "overdue": due < as_of_date,
                    "frequency": rec["frequency"],
                }
            )
    instances.sort(key=lambda item: (item["due_date"], item["name"]))
    return {
        "read_only": True,
        "as_of": as_of,
        "within_days": within,
        "window_end": window_end.isoformat(),
        "currency": code,
        "count": len(instances),
        "instances": instances,
    }


def cmd_run(conn: sqlite3.Connection, args: argparse.Namespace) -> Dict[str, Any]:
    code = _lib.resolve_currency(conn, args.currency)
    until = _lib.validate_date(args.until, "--until") if args.until else _lib.today_str()
    until_date = date.fromisoformat(until)
    dry_run = bool(args.dry_run)
    rows = conn.execute("SELECT * FROM recurring WHERE active = 1 ORDER BY next_due").fetchall()
    posted: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    planned: List[Dict[str, Any]] = []
    advanced: List[Dict[str, Any]] = []

    for rec in rows:
        due_dates, next_after = _occurrences(rec, until_date)
        if due_dates:
            for due in due_dates:
                due_iso = due.isoformat()
                logged = conn.execute(
                    "SELECT transaction_id FROM recurring_log WHERE recurring_id = ? AND due_date = ?",
                    (rec["id"], due_iso),
                ).fetchone()
                if logged:
                    skipped.append(
                        {"recurring_id": rec["id"], "name": rec["name"], "due_date": due_iso}
                    )
                    continue
                base = _recurring_dict(conn, rec, code)
                item = {
                    "recurring_id": rec["id"],
                    "name": rec["name"],
                    "account": base["account"],
                    "category": base["category"],
                    "due_date": due_iso,
                    "amount_units": base["amount_units"],
                    "amount": base["amount"],
                }
                if dry_run:
                    planned.append(item)
                    continue
                tx = conn.execute(
                    "INSERT INTO transactions (account_id, date, amount_units, category_id, payee, note) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        rec["account_id"],
                        due_iso,
                        rec["amount_units"],
                        rec["category_id"],
                        rec["name"],
                        "recurring: %s" % rec["name"],
                    ),
                ).lastrowid
                conn.execute(
                    "INSERT OR IGNORE INTO recurring_log (recurring_id, due_date, transaction_id) "
                    "VALUES (?, ?, ?)",
                    (rec["id"], due_iso, tx),
                )
                item["transaction_id"] = tx
                posted.append(item)
            if not dry_run:
                conn.execute(
                    "UPDATE recurring SET next_due = ? WHERE id = ?",
                    (next_after.isoformat(), rec["id"]),
                )
                advanced.append(
                    {"recurring_id": rec["id"], "name": rec["name"], "next_due": next_after.isoformat()}
                )

    return {
        "dry_run": dry_run,
        "until": until,
        "currency": code,
        "posted_count": len(posted),
        "posted": posted,
        "planned_count": len(planned),
        "planned": planned,
        "skipped_count": len(skipped),
        "skipped": skipped,
        "advanced": advanced,
    }


def _validate_hhmm(value: str) -> Tuple[int, int]:
    text = (value or "").strip()
    parts = text.split(":")
    if len(parts) != 2:
        raise _lib.CommandError("invalid_time", "--at-time must look like HH:MM")
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        raise _lib.CommandError("invalid_time", "--at-time must look like HH:MM")
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise _lib.CommandError("invalid_time", "--at-time must be a valid 24-hour time")
    return hour, minute


def _xml_escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _script_path(path_mode: str) -> str:
    skill_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if path_mode == "abs":
        return os.path.join(skill_dir, "scripts", "recurring.py")
    if path_mode == "relative":
        return os.path.join("scripts", "recurring.py")
    return "{skillDir}/scripts/recurring.py"


def cmd_schedule_hint(conn: sqlite3.Connection, args: argparse.Namespace) -> Dict[str, Any]:
    target = args.target
    path_mode = args.path_mode or ("abs" if target in OS_TARGETS + ("nanobot",) else "skill")
    if path_mode not in ("abs", "relative", "skill"):
        raise _lib.CommandError("invalid_input", "--path-mode must be abs, relative, or skill")
    within = args.within if args.within is not None else 14
    if within < 0:
        raise _lib.CommandError("invalid_input", "--within must be zero or greater")
    hour, minute = _validate_hhmm(args.at_time or "08:00")
    tz = args.tz or os.environ.get("TZ") or "UTC"
    name = args.name or "personal-finance-daily"
    script = _script_path(path_mode)
    db_path = str(_lib.resolve_db_path(args.db))
    today = _lib.today_str()
    cron_expr = "%d %d * * *" % (minute, hour)
    python = "/usr/bin/env python3" if target in OS_TARGETS else "python3"

    run_now = "%s %s run --until %s --db %s" % (python, script, today, db_path)
    due_now = "%s %s due --within %d --db %s" % (python, script, within, db_path)
    commands = [
        {"step": "post", "description": "Post due recurring transactions (idempotent, writes)", "command": run_now},
        {"step": "report", "description": "List due and upcoming bills (read-only)", "command": due_now},
    ]

    base: Dict[str, Any] = {
        "target": target,
        "path_mode": path_mode,
        "generated_at": today,
        "skill_dir": os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "script": script,
        "db": db_path,
        "cron_expr": cron_expr,
        "tz": tz,
        "commands": commands,
        "installs_nothing": True,
    }

    if target in ("generic", "claude", "codex"):
        instructions = (
            "Register a recurring task with your host's scheduling mechanism (a cron tool, "
            "reminders, a task runner, or an OS scheduler such as the crontab, systemd, or "
            "launchd targets). On each trigger, run the post command, then the report command, "
            "and report posted plus upcoming bills. Stay silent when nothing is due. Never "
            "compute amounts yourself; the script owns the ledger."
        )
        base["instructions"] = instructions
        base["notes"] = [
            "This target installs nothing; the agent or user registers it.",
            "Amounts and dates are resolved by recurring.py, not by the scheduler.",
            "The report command is read-only; only the post command writes.",
        ]
        if target in ("claude", "codex"):
            base["fallback"] = (
                "If this host has no native scheduler, use the crontab, launchd, or systemd "
                "target and register the emitted artifact."
            )
        return base

    if target == "nanobot":
        message = (
            "$personal-finance Post due recurring transactions and report upcoming bills. "
            "Run: %s ; then run: %s ; report what was posted and what is due next. "
            "Stay silent if nothing is due." % (run_now, due_now)
        )
        base["name"] = name
        base["message"] = message
        base["cron_tool"] = {
            "action": "add",
            "message": message,
            "cron_expr": cron_expr,
            "tz": tz,
        }
        base["instructions"] = (
            "Create this job with your host's cron tool using the cron_tool fields above. "
            "This is the only target that uses the $personal-finance explicit invocation."
        )
        base["notes"] = [
            "Jobs must be created from a live chat session; a script cannot create them.",
            "cron tool tz is only valid together with cron_expr, which is why both are emitted.",
            "Remove the job through the cron tool; that does not delete the recurring rules in the ledger.",
        ]
        return base

    if target == "crontab":
        run_cmd = "%s %s run --until \"$(date +\\%%F)\" --db %s --quiet" % (python, script, db_path)
        due_cmd = "%s %s due --within %d --db %s --quiet" % (python, script, within, db_path)
        lines = [
            "%d %d * * * %s" % (minute, hour, run_cmd),
            "%d %d * * * %s" % ((minute + 5) % 60, hour, due_cmd),
        ]
        base["install"] = {"type": "crontab", "lines": lines}
        base["instructions"] = (
            "Append these lines to your crontab (crontab -e). They run the script directly and "
            "produce no chat notification. Remove them with crontab -e; that does not change the "
            "recurring rules in the ledger."
        )
        base["notes"] = [
            "The run line is idempotent and catches up missed occurrences after downtime.",
            "--quiet keeps a silent no-op from generating cron email.",
        ]
        return base

    if target == "systemd":
        run_cmd = (
            "/usr/bin/env python3 %s run --until \"$(date +%%%%F)\" --db %s --quiet && "
            "/usr/bin/env python3 %s due --within %d --db %s --quiet"
            % (script, db_path, script, within, db_path)
        )
        service = (
            "[Unit]\n"
            "Description=Post personal-finance recurring transactions\n\n"
            "[Service]\n"
            "Type=oneshot\n"
            "ExecStart=/bin/sh -c '%s'\n" % run_cmd
        )
        timer = (
            "[Unit]\n"
            "Description=Daily personal-finance recurring postings\n\n"
            "[Timer]\n"
            "OnCalendar=*-*-* %02d:%02d:00\n"
            "Persistent=true\n\n"
            "[Install]\n"
            "WantedBy=timers.target\n" % (hour, minute)
        )
        base["install"] = {
            "type": "systemd",
            "files": {
                "personal-finance-recurring.service": service,
                "personal-finance-recurring.timer": timer,
            },
            "commands": [
                "systemctl --user daemon-reload",
                "systemctl --user enable --now personal-finance-recurring.timer",
            ],
        }
        base["instructions"] = (
            "Write both files to ~/.config/systemd/user/, then run the install commands. "
            "This produces no chat notification; remove with systemctl --user disable --now "
            "personal-finance-recurring.timer."
        )
        base["notes"] = [
            "Persistent=true catches up missed runs after the machine sleeps.",
            "The service runs the script directly and writes nothing to chat.",
        ]
        return base

    # launchd
    run_cmd = (
        "/usr/bin/env python3 %s run --until \"$(date +%%F)\" --db %s --quiet && "
        "/usr/bin/env python3 %s due --within %d --db %s --quiet"
        % (script, db_path, script, within, db_path)
    )
    plist = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        "<dict>\n"
        "  <key>Label</key>\n"
        "  <string>com.personal-finance.recurring</string>\n"
        "  <key>ProgramArguments</key>\n"
        "  <array>\n"
        "    <string>/bin/sh</string>\n"
        "    <string>-c</string>\n"
        "    <string>%s</string>\n"
        "  </array>\n"
        "  <key>StartCalendarInterval</key>\n"
        "  <dict>\n"
        "    <key>Hour</key>\n"
        "    <integer>%d</integer>\n"
        "    <key>Minute</key>\n"
        "    <integer>%d</integer>\n"
        "  </dict>\n"
        "</dict>\n"
        "</plist>\n" % (_xml_escape(run_cmd), hour, minute)
    )
    base["install"] = {
        "type": "launchd",
        "files": {"com.personal-finance.recurring.plist": plist},
        "commands": [
            "cp com.personal-finance.recurring.plist ~/Library/LaunchAgents/",
            "launchctl load ~/Library/LaunchAgents/com.personal-finance.recurring.plist",
        ],
    }
    base["instructions"] = (
        "Write the plist to ~/Library/LaunchAgents/ and load it with launchctl. This produces "
        "no chat notification; unload with launchctl unload."
    )
    base["notes"] = [
        "launchd runs missed jobs on wake when the machine was asleep.",
        "The job runs the script directly and writes nothing to chat.",
    ]
    return base


def cmd_pause(conn: sqlite3.Connection, args: argparse.Namespace) -> Dict[str, Any]:
    code = _lib.resolve_currency(conn, args.currency)
    row = resolve_recurring(conn, args.recurring)
    conn.execute("UPDATE recurring SET active = 0 WHERE id = ?", (row["id"],))
    updated = conn.execute("SELECT * FROM recurring WHERE id = ?", (row["id"],)).fetchone()
    data = _recurring_dict(conn, updated, code)
    data["note"] = "Pausing stops future postings but leaves any scheduled host job in place."
    return data


def cmd_resume(conn: sqlite3.Connection, args: argparse.Namespace) -> Dict[str, Any]:
    code = _lib.resolve_currency(conn, args.currency)
    row = resolve_recurring(conn, args.recurring)
    conn.execute("UPDATE recurring SET active = 1 WHERE id = ?", (row["id"],))
    updated = conn.execute("SELECT * FROM recurring WHERE id = ?", (row["id"],)).fetchone()
    return _recurring_dict(conn, updated, code)


def cmd_delete(conn: sqlite3.Connection, args: argparse.Namespace) -> Dict[str, Any]:
    row = resolve_recurring(conn, args.recurring)
    log_count = conn.execute(
        "SELECT COUNT(*) AS n FROM recurring_log WHERE recurring_id = ?", (row["id"],)
    ).fetchone()["n"]
    conn.execute("DELETE FROM recurring_log WHERE recurring_id = ?", (row["id"],))
    conn.execute("DELETE FROM recurring WHERE id = ?", (row["id"],))
    return {
        "deleted": True,
        "recurring_id": row["id"],
        "name": row["name"],
        "log_rows_removed": log_count,
        "note": "Deleting a rule does not remove any scheduled host job; cancel that separately.",
    }


def build_parser() -> argparse.ArgumentParser:
    common = _lib.common_parser()
    parser = argparse.ArgumentParser(
        prog="recurring.py", description="Manage recurring transactions and scheduling hints."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    add = subparsers.add_parser(
        "add", help="create a recurring rule", parents=[common],
        description="Create a recurring transaction rule.",
    )
    add.add_argument("--name", required=True)
    add.add_argument("--account", required=True)
    add.add_argument("--amount", required=True, help="positive amount (magnitude)")
    add.add_argument("--category")
    add.add_argument("--direction", choices=list(_lib.DIRECTIONS))
    add.add_argument("--frequency", required=True, choices=list(_lib.FREQUENCIES))
    add.add_argument("--interval", type=int, default=1)
    add.add_argument("--start", help="first due date YYYY-MM-DD (default today)")
    add.add_argument("--end", help="optional last due date YYYY-MM-DD")
    add.add_argument("--note")
    add.set_defaults(func=cmd_add, verb="add")

    listing = subparsers.add_parser(
        "list", help="list recurring rules", parents=[common],
        description="List recurring rules (active by default).",
    )
    listing.add_argument("--include-inactive", action="store_true")
    listing.set_defaults(func=cmd_list, verb="list")

    due = subparsers.add_parser(
        "due", help="list due and upcoming bills (read-only)", parents=[common],
        description="List due and upcoming recurring instances within a window (read-only).",
    )
    due.add_argument("--within", type=int, default=14, help="days ahead (default 14)")
    due.add_argument("--as-of", help="reference date YYYY-MM-DD (default today)")
    due.set_defaults(func=cmd_due, verb="due")

    run = subparsers.add_parser(
        "run", help="post due recurring transactions (idempotent)", parents=[common],
        description="Post all recurring occurrences up to a date. Idempotent via recurring_log.",
    )
    run.add_argument("--until", help="post through this date YYYY-MM-DD (default today)")
    run.add_argument("--dry-run", action="store_true")
    run.set_defaults(func=cmd_run, verb="run")

    hint = subparsers.add_parser(
        "schedule-hint", help="emit a scheduling artifact (never installs)", parents=[common],
        description="Emit an installable scheduling artifact for a chosen host or OS scheduler.",
    )
    hint.add_argument("--target", choices=list(SCHEDULE_TARGETS), default="generic")
    hint.add_argument("--path-mode", choices=("abs", "relative", "skill"))
    hint.add_argument("--within", type=int, default=14)
    hint.add_argument("--at-time", default="08:00", help="daily time HH:MM (default 08:00)")
    hint.add_argument("--tz", help="timezone name for cron targets (default $TZ or UTC)")
    hint.add_argument("--name", help="job name for the nanobot target")
    hint.set_defaults(func=cmd_schedule_hint, verb="schedule-hint")

    pause = subparsers.add_parser(
        "pause", help="pause a recurring rule", parents=[common],
        description="Pause a recurring rule so it stops posting.",
    )
    pause.add_argument("recurring", help="recurring id or name")
    pause.set_defaults(func=cmd_pause, verb="pause")

    resume = subparsers.add_parser(
        "resume", help="resume a recurring rule", parents=[common],
        description="Resume a paused recurring rule.",
    )
    resume.add_argument("recurring", help="recurring id or name")
    resume.set_defaults(func=cmd_resume, verb="resume")

    delete = subparsers.add_parser(
        "delete", help="delete a recurring rule", parents=[common],
        description="Delete a recurring rule and its posting log.",
    )
    delete.add_argument("recurring", help="recurring id or name")
    delete.set_defaults(func=cmd_delete, verb="delete")

    return parser


def main() -> None:
    _lib.run_script("recurring", build_parser())


if __name__ == "__main__":
    main()
