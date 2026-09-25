---
name: personal-finance
description: Track personal money in a local SQLite ledger with one user-chosen currency. Use when logging or editing accounts, income, expenses, transfers, categories, budgets, or recurring bills, and when producing spending, cashflow, or net-worth reports or importing or exporting transactions as CSV.
license: MIT
---

# Personal finance

Manage a private, local-first money ledger. All executable behavior lives in
scripts; you run them and read their JSON output. The ledger uses one currency
chosen by the user.

## Running the scripts

Resolve the script directory from the loaded skill directory (the folder that
contains this `SKILL.md`), then run:

```
python3 <skill-dir>/scripts/<domain>.py <verb> [flags]
```

- `<domain>` is one of `init`, `settings`, `accounts`, `categories`,
  `transactions`, `budgets`, `recurring`, `reports`, `csv_io`.
- There is no placeholder substitution for the skill directory. Use the real
  path to the loaded skill folder.
- Every script accepts `--db PATH`, `--format json|table`, `--currency CODE`,
  and `--quiet`.

## Output contract

Every run prints exactly one JSON envelope on stdout.

- Success: `{"ok": true, "command": "<domain>.<verb>", "data": ...}`, exit 0.
- Failure: `{"ok": false, "error": {"code": ..., "message": ...}}`, exit 1.
- Bad flags: argparse usage error, exit 2.

Parse `data`. On `"ok": false`, read `error.message` and fix the call; do not
guess at partial output. Use `--format table` only when showing a person.

## Money rules

- Never do money arithmetic in your head or in the response. The scripts own
  all amounts and formatting; they return both integer minor units
  (`*_units`) and a formatted string.
- Transaction and recurring amounts are a **positive magnitude** plus a
  direction. `--direction out` is money leaving an account; `--direction in`
  is money arriving. When you pass a `--category`, the direction is derived
  from its kind, so `--direction` can be omitted.
- Budgets and transfers take positive amounts too.
- CSV import treats amounts as signed (negative means outflow), matching
  exports, so re-importing an export does not flip directions.
- Amounts are stored in integer minor units using the ledger currency's
  exponent (0 for JPY, 3 for KWD, 2 otherwise). Pass plain decimal strings.

## First run

If the ledger is new, initialize it and set the user's currency:

```
python3 <skill-dir>/scripts/init.py init --currency <CODE>
python3 <skill-dir>/scripts/settings.py show
```

Ask the user for their currency if you do not know it. Do not silently assume
USD. The ledger currency is global, not per account.

Changing the currency later (`settings.py set --currency <CODE>`) only
relabels existing amounts; it never converts them. Confirm with the user
before changing it on a ledger that already has transactions.

## Choosing a script

| The user wants to... | Run |
|---|---|
| set up the ledger / choose currency | `init.py init --currency CODE` |
| see or change settings, currency | `settings.py show\|get\|set` |
| add, list, rename, archive an account | `accounts.py add\|list\|show\|rename\|archive` |
| see balances or net worth by account | `accounts.py balance` or `reports.py networth` |
| add or manage categories | `categories.py add\|list\|rename\|archive` |
| log an expense, income, or refund | `transactions.py add` |
| move money between own accounts | `transactions.py transfer` |
| find, change, or remove a transaction | `transactions.py list\|edit\|delete` |
| set or check a monthly budget | `budgets.py set\|list\|status` |
| set up a recurring bill or income | `recurring.py add\|list\|due` |
| post due recurring transactions | `recurring.py run` |
| plan a schedule to run recurring postings | `recurring.py schedule-hint` |
| summarize a month, cashflow, or spending by category | `reports.py summary\|cashflow\|categories` |
| import or export CSV | `csv_io.py import\|export` |

Run `python3 <skill-dir>/scripts/<domain>.py --help` or read
`references/commands.md` for every flag.

## Safety habits

- Use `--dry-run` before `csv_io.py import` and `recurring.py run` when the
  effect is not obvious, then show the result.
- Confirm with the user before `delete` or `archive`, and before importing
  with `--force`.
- Prefer `transactions.py list` filters over pulling the whole ledger.
- Balances come from `accounts.py balance` or `reports.py networth`; do not
  add transactions up yourself.

## Recurring bills and scheduling

Recurring rules live in the ledger database; they are the source of truth for
what repeats and when. The scripts never install or change any scheduler.

- `recurring.py due` is read-only and lists due and upcoming instances.
- `recurring.py run` posts occurrences up to a date and is idempotent, so it
  is safe to trigger daily and catches up after downtime.
- To automate postings or reminders, ask the host's scheduling mechanism to
  trigger `recurring.py run`, then read `recurring.py due`. Generate the
  artifact with `recurring.py schedule-hint --target ...`; it emits what to
  register and installs nothing.
- Removing a scheduled trigger does not delete recurring rules, and deleting a
  rule does not remove a trigger. Cancel the correct one.

Read `references/scheduling.md` for the target matrix and install or cancel
steps. Name no specific host tool in a general instruction.

## Data location and backup

The ledger is a single SQLite file at, in order of precedence: `--db PATH`,
`$PERSONAL_FINANCE_DB`, then `~/.local/share/personal-finance/finance.db`.
Back up by copying that file. The repo ships no data.

## References

- `references/commands.md`: full CLI reference, all verbs and flags.
- `references/schema.md`: database schema, currency and exponent rules, CSV
  column mapping.
- `references/scheduling.md`: host-agnostic scheduling targets and artifacts.
