# Schema and conventions

## Storage

A single SQLite database (WAL mode, foreign keys on, `busy_timeout=5000`).
Path precedence: `--db PATH`, `$PERSONAL_FINANCE_DB`, then
`~/.local/share/personal-finance/finance.db`. The database is created on first
run; `init.py init` seeds categories and sets the currency. Back up by copying
the file. `PRAGMA user_version` is `1`.

## Tables

```
meta(key PK, value)

accounts(
  id PK, name UNIQUE, type CHECK in
    (checking,savings,credit,cash,investment,loan),
  institution, opening_balance_units INTEGER DEFAULT 0,
  archived DEFAULT 0, created_at)

categories(
  id PK, name, kind CHECK in (income,expense),
  parent_id FK categories, archived DEFAULT 0,
  UNIQUE(name, kind))

transactions(
  id PK, account_id FK accounts, date, amount_units INTEGER,
  category_id FK categories NULL, payee, note, tags,
  transfer_group NULL, created_at)
  -- indexes: date, account_id, category_id, transfer_group

budgets(
  id PK, category_id FK categories, month, amount_units INTEGER,
  UNIQUE(category_id, month))

recurring(
  id PK, name, account_id FK accounts, amount_units INTEGER,
  category_id FK categories NULL,
  frequency CHECK in (daily,weekly,monthly,yearly),
  interval DEFAULT 1, next_due, end_date NULL, note,
  active DEFAULT 1, created_at)

recurring_log(
  recurring_id FK recurring, due_date, transaction_id FK transactions,
  created_at, PRIMARY KEY(recurring_id, due_date))
```

Seeded categories: expense `Groceries, Dining, Rent, Utilities, Transport,
Health, Entertainment, Shopping, Subscriptions, Travel, Fees, Taxes, Other`;
income `Salary, Freelance, Interest, Gifts, Other Income`.

## Money and currency

- One ledger currency per database, stored in `meta.currency`. There are no
  per-account currencies and no FX tables.
- Resolution order: `--currency` flag, `$PERSONAL_FINANCE_CURRENCY`, the
  stored value, then `USD` before the first `init`.
- Amounts are integer **minor units**. The exponent comes from a small
  built-in ISO 4217 table: `0` for JPY, KRW, VND and other zero-decimal
  currencies; `3` for BHD, KWD, OMR, and similar; `2` otherwise, including
  unknown codes.
- Example: with `IDR` (exponent 2), `--amount 1250000` stores `125000000`;
  with `JPY` (exponent 0), `--amount 1250` stores `1250`.
- Output always carries both `*_units` and a formatted string like
  `IDR 1,250,000.00` or `JPY 1,250`.
- Changing the currency only relabels stored amounts. It never converts them.
  `settings.py set` emits a warning and the skill asks the user to confirm.

## Direction, signs, and reports

- `transactions` amounts are signed: negative is money out, positive is money
  in. The `add` verb takes a positive magnitude and a direction (explicit
  `--direction`, or derived from the category kind).
- `transfers` write two rows sharing a `transfer_group` (negative from,
  positive to) with no category. They are excluded from income and expense
  totals but do move account balances.
- `budgets.amount_units` is a positive limit. `budgets status` spent is the
  negation of the sum of expense transactions in that category and month.
- `networth` sums signed balances. Credit and loan accounts normally carry
  negative balances, so debt reduces net worth; asset and liability subtotals
  are reported separately.
- `recurring.amount_units` is signed and follows the same direction rules as
  transactions.

## Recurring idempotency

`run` computes occurrences from `next_due` forward, inserts a transaction per
occurrence, records `(recurring_id, due_date)` in `recurring_log`, and advances
`next_due`. The log primary key means a repeated `run` never double-posts, and
`--until` lets a daily trigger catch up after downtime. `due` and
`schedule-hint` are read-only.

## CSV mapping

Export columns: `date, account, amount, category, payee, note, tags,
transfer_group`, with signed decimal amounts.

Import:
- Date column defaults to `date`, format `YYYY-MM-DD`; override with
  `--date-column` and/or `--date-format` (Python `strptime`).
- Amount column defaults to `amount`, parsed as signed decimal in the ledger
  currency.
- Optional columns `payee`, `note`, `category`, `tags` come from a `--mapping`
  JSON object, for example
  `--mapping '{"date":"Date","amount":"Amount","payee":"Description"}'`.
- A row is a duplicate when its account, date, amount, and payee already
  exist; duplicates are skipped unless `--force`.
- Unknown category names found in the file are created with a kind inferred
  from the sign (negative is expense, positive is income).
