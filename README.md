# personal-finance-skill

A portable [Agent Skill](https://skills.sh) for managing personal finances in a
local SQLite ledger, with one currency chosen by the user. It works with any
Agent Skills host (nanobot, Claude Code, Codex, Kilo, OpenClaw, and others).

Everything executable lives in focused Python scripts under
`personal-finance/scripts/`. The agent runs a script and reads its JSON output.
There is no server, no network access, and no third-party dependency: the
standard library only (Python 3.9+).

## What it does

- Accounts and balances: checking, savings, credit, cash, investment, loan
- Income, expenses, refunds, and transfers between your own accounts
- Categories, monthly budgets, and budget status
- Recurring bills and income, with idempotent catch-up posting
- Reports: monthly summary, spending by category, cashflow, net worth
- CSV import and export with duplicate skipping

Scheduling is opt-in and one-directional: the skill emits an installable
artifact for a host scheduler or an OS scheduler (`recurring.py schedule-hint`)
and never installs or mutates a scheduler itself.

## Install

With the skills.sh CLI, replacing `OWNER` with the GitHub owner after
publishing:

```bash
npx --yes skills@latest add OWNER/personal-finance-skill \
  --skill personal-finance --agent <your-agent> --copy --yes
```

For nanobot specifically, `--agent openclaw` is the marketplace agent id, and
it copies into `<workspace>/skills`:

```bash
npx --yes skills@latest add OWNER/personal-finance-skill \
  --skill personal-finance --agent openclaw --copy --yes
```

Manual install: copy the `personal-finance/` folder into your host's skills
directory so that `personal-finance/SKILL.md` is discoverable. The folder name
must stay `personal-finance`.

## Quick start

```bash
SKILL=./personal-finance
python3 "$SKILL/scripts/init.py" init --currency IDR
python3 "$SKILL/scripts/accounts.py" add --name "Everyday" --type checking
python3 "$SKILL/scripts/transactions.py" add --account "Everyday" \
  --amount 42.50 --date 2026-01-15 --category Groceries --payee "Corner shop"
python3 "$SKILL/scripts/reports.py" summary --month 2026-01
```

Replace `IDR` with any ISO-style code (`EUR`, `JPY`, `NGN`, ...). The ledger is
a single currency; changing it later relabels amounts, it does not convert
them.

Verify the currency and data location:

```bash
python3 "$SKILL/scripts/settings.py" show
```

## Requirements

- Python 3.9 or newer, standard library only
- A writable location for the database (default
  `~/.local/share/personal-finance/finance.db`)

## Where the data lives

Resolved in order: `--db PATH`, `$PERSONAL_FINANCE_DB`, then
`~/.local/share/personal-finance/finance.db`. Back up by copying that one file.
No personal data is stored in this repository.

## Recurring bills

```bash
python3 "$SKILL/scripts/recurring.py" add --name "Internet" --account "Everyday" \
  --amount 30 --frequency monthly --category Utilities
python3 "$SKILL/scripts/recurring.py" due --within 14
python3 "$SKILL/scripts/recurring.py" run --until 2026-02-01
python3 "$SKILL/scripts/recurring.py" schedule-hint --target generic
```

`run` is idempotent, so it is safe to trigger daily; it catches up after
downtime. `schedule-hint` emits an artifact for the target and installs
nothing. See `personal-finance/references/scheduling.md`.

## Layout

```
personal-finance-skill/
├── README.md
├── LICENSE
├── .gitignore
├── tests/smoke.sh
└── personal-finance/
    ├── SKILL.md
    ├── scripts/       # _lib.py + one script per domain
    └── references/    # commands.md, schema.md, scheduling.md
```

## Test

```bash
bash tests/smoke.sh
```

The harness runs the scripts end to end against a throwaway database and
checks balances, idempotency, currency formatting, scheduling artifacts, and
error handling.

## License

MIT. See `LICENSE`.
