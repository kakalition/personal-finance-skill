# Command reference

Run every script as `python3 <skill-dir>/scripts/<domain>.py <verb> [flags]`.

Shared flags (accepted by all verbs):

- `--db PATH` — database file (default: `$PERSONAL_FINANCE_DB` or
  `~/.local/share/personal-finance/finance.db`)
- `--format json|table` — output format (default `json`)
- `--currency CODE` — override the ledger currency for this call
- `--quiet` — suppress success output; errors still print

Every script auto-creates the database, parent directories, and schema on
first use. `init.py init` additionally seeds default categories and the
currency.

## init.py

| Verb | Flags |
|---|---|
| `init` | `--currency CODE` (set the ledger currency) |

Creates the ledger if needed. Idempotent.

## settings.py

| Verb | Flags |
|---|---|
| `show` | — |
| `get` | `--key NAME` (omit for the whole settings map) |
| `set` | `--currency CODE` (relabels existing amounts; does not convert) |

`data.warning` is present when `set` changes an existing currency.

## accounts.py

| Verb | Flags |
|---|---|
| `add` | `--name NAME --type TYPE [--institution NAME] [--opening-balance AMOUNT]` |
| `list` | `[--include-archived]` |
| `show` | `<account>` |
| `rename` | `<account> --name NAME` |
| `archive` | `<account>` |
| `balance` | `[--as-of DATE] [--include-archived]` |

`<account>` accepts an id or a name. `TYPE` is one of `checking`, `savings`,
`credit`, `cash`, `investment`, `loan`. Opening balance is signed (negative
for debt). `balance` returns per-account balances plus asset, liability, and
total figures.

## categories.py

| Verb | Flags |
|---|---|
| `add` | `--name NAME --kind income\|expense [--parent ID_OR_NAME]` |
| `list` | `[--kind income\|expense] [--include-archived]` |
| `rename` | `<category> --name NAME` |
| `archive` | `<category>` |

A parent must share the child's kind.

## transactions.py

| Verb | Flags |
|---|---|
| `add` | `--account A --amount MAGNITUDE --date DATE [--category C] [--direction in\|out] [--payee P] [--note N] [--tags a,b]` |
| `transfer` | `--from A --to B --amount MAGNITUDE --date DATE [--note N]` |
| `list` | `[--account A] [--from DATE] [--to DATE] [--category C] [--search TEXT] [--limit N]` |
| `edit` | `<transaction_id> [--account A] [--amount M] [--date D] [--category C] [--direction in\|out] [--payee P] [--note N] [--tags a,b]` |
| `delete` | `<transaction_id>` |

Amounts are positive magnitudes plus a direction. If `--category` is given,
the direction is derived from its kind and may be omitted. A transfer writes
two linked rows and is excluded from income/expense reports. Editing a
transfer is rejected; delete it and create a new one. Deleting either side of
a transfer deletes both.

## budgets.py

| Verb | Flags |
|---|---|
| `set` | `--category C --month YYYY-MM --amount LIMIT` |
| `list` | `[--month YYYY-MM]` |
| `status` | `[--month YYYY-MM]` |

Budgets apply to expense categories. `status` reports limit, spent, remaining,
and percent used for the month (default current month).

## recurring.py

| Verb | Flags |
|---|---|
| `add` | `--name N --account A --amount MAGNITUDE --frequency daily\|weekly\|monthly\|yearly [--interval N] [--start DATE] [--end DATE] [--category C] [--direction in\|out] [--note TEXT]` |
| `list` | `[--include-inactive]` |
| `due` | `[--within DAYS] [--as-of DATE]` (read-only) |
| `run` | `[--until DATE] [--dry-run]` (idempotent; the only writer) |
| `schedule-hint` | `--target TARGET [--path-mode abs\|relative\|skill] [--within DAYS] [--at-time HH:MM] [--tz ZONE] [--name JOB]` (see `references/scheduling.md` for targets) |
| `pause` | `<recurring_id_or_name>` |
| `resume` | `<recurring_id_or_name>` |
| `delete` | `<recurring_id_or_name>` |

`run` posts every occurrence up to `--until` (default today). The
`recurring_log` table makes it idempotent, so repeated runs never double-post
and a daily trigger catches up after downtime. `due` and `schedule-hint` never
write. Deleting a rule removes its log but not any scheduled trigger.

## reports.py

| Verb | Flags |
|---|---|
| `summary` | `[--month YYYY-MM]` |
| `categories` | `[--month YYYY-MM] [--kind income\|expense]` |
| `cashflow` | `--from DATE --to DATE [--group-by month\|week]` |
| `networth` | `[--as-of DATE] [--by-account] [--include-archived]` |

Transfers are excluded from income and expense totals. Net worth sums signed
account balances, so credit and loan debt (negative balances) reduces it.

## csv_io.py

| Verb | Flags |
|---|---|
| `export` | `[--from DATE] [--to DATE] [--account A] [--output PATH]` |
| `import` | `FILE --account A [--date-column C] [--amount-column C] [--date-format FMT] [--delimiter CHAR] [--mapping JSON] [--category C] [--dry-run] [--force]` |

Export writes to `--output` or returns CSV text in `data.csv`. Columns are
`date, account, amount, category, payee, note, tags, transfer_group`.

Import maps columns with `--date-column`/`--amount-column` or a `--mapping`
JSON object that may also map `payee`, `note`, `category`, and `tags`. The
date column defaults to `date` (YYYY-MM-DD) and the amount column to `amount`
(signed; negative means outflow). Rows are skipped as duplicates when the
account, date, amount, and payee already exist. Without `--force`, any invalid
row aborts the whole import; with `--force`, invalid rows are skipped and
duplicates are imported.
