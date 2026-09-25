#!/usr/bin/env bash
# End-to-end smoke test for the personal-finance skill.
#
# Runs every script and verb against throwaway databases and checks output,
# hand-computed balances, idempotency, currency exponents, CSV behavior,
# scheduling artifacts (including executing them twice), output formats, and
# error handling. No network access; bash + python3 stdlib only.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SKILL="$ROOT/personal-finance"
SCRIPTS="$SKILL/scripts"
PY="${PY:-python3}"

PASS=0
FAIL=0
TMPDIR_SMOKE="$(mktemp -d "${TMPDIR:-/tmp}/pf-smoke.XXXXXX")"
cleanup() { rm -rf "$TMPDIR_SMOKE"; }
trap cleanup EXIT

pass() { PASS=$((PASS + 1)); }
fail() { FAIL=$((FAIL + 1)); printf 'FAIL: %s\n' "$*" >&2; }
section() { printf '\n== %s ==\n' "$*"; }

check() { # check DESC ACTUAL EXPECTED
  if [ "$2" = "$3" ]; then pass; else fail "$1: expected [$3] got [$2]"; fi
}

# jqv '<python expression using d>' reads JSON on stdin.
jqv() { "$PY" -c 'import json,sys
d=json.load(sys.stdin)
print(eval(sys.argv[1]))' "$1"; }

run() { "$PY" "$@"; }

# expect_fail DESC ERROR_CODE CMD...
expect_fail() {
  local desc="$1" code="$2"; shift 2
  local out rc
  out="$("$@" 2>/dev/null)"; rc=$?
  if [ "$rc" -eq 1 ]; then pass; else fail "$desc: expected exit 1, got $rc"; fi
  check "$desc code" "$(printf '%s' "$out" | jqv "d['error']['code']")" "$code"
}

# expect_usage DESC CMD...  (argparse errors exit 2)
expect_usage() {
  local desc="$1"; shift
  "$@" >/dev/null 2>&1; local rc=$?
  if [ "$rc" -eq 2 ]; then pass; else fail "$desc: expected exit 2, got $rc"; fi
}

unset PERSONAL_FINANCE_CURRENCY 2>/dev/null || true

# ===========================================================================
section "init / settings"
export PERSONAL_FINANCE_DB="$TMPDIR_SMOKE/main.db"
OUT="$(run "$SCRIPTS/init.py" init --currency IDR)"
check "init ok" "$(printf '%s' "$OUT" | jqv "d['ok']")" "True"
check "init currency" "$(printf '%s' "$OUT" | jqv "d['data']['currency']")" "IDR"
check "init exponent" "$(printf '%s' "$OUT" | jqv "d['data']['currency_exponent']")" "2"
check "init seeded categories" "$(printf '%s' "$OUT" | jqv "d['data']['categories_seeded'] > 0")" "True"

OUT="$(run "$SCRIPTS/init.py" init --currency IDR)"
check "init idempotent" "$(printf '%s' "$OUT" | jqv "d['data']['already_initialized']")" "True"

OUT="$(run "$SCRIPTS/settings.py" show)"
check "settings currency" "$(printf '%s' "$OUT" | jqv "d['data']['currency']")" "IDR"
check "settings sample" "$(printf '%s' "$OUT" | jqv "d['data']['sample_amount']")" "IDR 1,250.00"
check "settings initialized" "$(printf '%s' "$OUT" | jqv "d['data']['initialized']")" "True"

OUT="$(run "$SCRIPTS/settings.py" get --key currency)"
check "settings get key" "$(printf '%s' "$OUT" | jqv "d['data']['value']")" "IDR"
OUT="$(run "$SCRIPTS/settings.py" get)"
check "settings get all" "$(printf '%s' "$OUT" | jqv "d['data']['currency']")" "IDR"
expect_fail "settings get unknown key" "not_found" run "$SCRIPTS/settings.py" get --key nope

# ===========================================================================
section "accounts"
run "$SCRIPTS/accounts.py" add --name Everyday --type checking >/dev/null
run "$SCRIPTS/accounts.py" add --name Savings --type savings --opening-balance 1000.00 >/dev/null
run "$SCRIPTS/accounts.py" add --name Card --type credit >/dev/null
run "$SCRIPTS/accounts.py" add --name Loan --type loan --opening-balance -500.00 >/dev/null

OUT="$(run "$SCRIPTS/accounts.py" list)"
check "accounts count" "$(printf '%s' "$OUT" | jqv "len(d['data'])")" "4"
OUT="$(run "$SCRIPTS/accounts.py" show Savings)"
check "account show opening" "$(printf '%s' "$OUT" | jqv "d['data']['opening_balance_units']")" "100000"
check "account show balance" "$(printf '%s' "$OUT" | jqv "d['data']['balance_units']")" "100000"
check "account show liability" "$(printf '%s' "$OUT" | jqv "d['data']['liability']")" "False"

OUT="$(run "$SCRIPTS/accounts.py" show Loan)"
check "loan negative opening" "$(printf '%s' "$OUT" | jqv "d['data']['balance_units']")" "-50000"
check "loan liability flag" "$(printf '%s' "$OUT" | jqv "d['data']['liability']")" "True"

OUT="$(run "$SCRIPTS/accounts.py" rename Savings --name "Emergency Fund")"
check "account rename" "$(printf '%s' "$OUT" | jqv "d['data']['name']")" "Emergency Fund"
run "$SCRIPTS/accounts.py" rename "Emergency Fund" --name Savings >/dev/null

run "$SCRIPTS/accounts.py" archive Card >/dev/null
OUT="$(run "$SCRIPTS/accounts.py" list)"
check "archived hidden" "$(printf '%s' "$OUT" | jqv "'Card' in [a['name'] for a in d['data']]")" "False"
OUT="$(run "$SCRIPTS/accounts.py" list --include-archived)"
check "archived shown" "$(printf '%s' "$OUT" | jqv "'Card' in [a['name'] for a in d['data']]")" "True"
run "$SCRIPTS/accounts.py" archive Card >/dev/null  # idempotent

expect_fail "duplicate account" "conflict" run "$SCRIPTS/accounts.py" add --name Everyday --type checking
expect_fail "unknown account" "not_found" run "$SCRIPTS/accounts.py" show Nope

# ===========================================================================
section "categories"
OUT="$(run "$SCRIPTS/categories.py" list --kind expense)"
check "seeded expense categories" "$(printf '%s' "$OUT" | jqv "'Groceries' in [c['name'] for c in d['data']]")" "True"
OUT="$(run "$SCRIPTS/categories.py" add --name "Coffee" --kind expense)"
check "category add" "$(printf '%s' "$OUT" | jqv "d['data']['kind']")" "expense"
expect_fail "duplicate category" "conflict" run "$SCRIPTS/categories.py" add --name Coffee --kind expense
expect_fail "parent kind mismatch" "invalid_category" run "$SCRIPTS/categories.py" add --name "Latte" --kind income --parent Groceries

OUT="$(run "$SCRIPTS/categories.py" add --name "Cafe" --kind expense --parent Dining)"
check "category parent" "$(printf '%s' "$OUT" | jqv "d['data']['parent']")" "Dining"
run "$SCRIPTS/categories.py" rename Cafe --name "Coffee Shop" >/dev/null
OUT="$(run "$SCRIPTS/categories.py" list --kind expense)"
check "category renamed" "$(printf '%s' "$OUT" | jqv "'Coffee Shop' in [c['name'] for c in d['data']]")" "True"
run "$SCRIPTS/categories.py" archive "Coffee Shop" >/dev/null
OUT="$(run "$SCRIPTS/categories.py" list --kind expense)"
check "category archived hidden" "$(printf '%s' "$OUT" | jqv "'Coffee Shop' in [c['name'] for c in d['data']]")" "False"
OUT="$(run "$SCRIPTS/categories.py" list --kind expense --include-archived)"
check "category archived shown" "$(printf '%s' "$OUT" | jqv "'Coffee Shop' in [c['name'] for c in d['data']]")" "True"

# ===========================================================================
section "transactions"
run "$SCRIPTS/transactions.py" add --account Everyday --amount 42.50 --date 2026-01-15 --category Groceries --payee "Corner shop" --tags "food,weekly" >/dev/null
run "$SCRIPTS/transactions.py" add --account Everyday --amount 5000.00 --date 2026-01-20 --category Salary --payee Acme >/dev/null
OUT="$(run "$SCRIPTS/transactions.py" add --account Everyday --amount 10.00 --date 2026-01-16 --direction out)"
check "explicit direction out" "$(printf '%s' "$OUT" | jqv "d['data']['amount_units']")" "-1000"
check "tags parsed" "$(printf '%s' "$OUT" | jqv "d['data']['tags']")" "[]"

OUT="$(run "$SCRIPTS/accounts.py" balance --as-of 2026-06-30)"
check "everyday after 3 tx" "$(printf '%s' "$OUT" | jqv "[a for a in d['data']['accounts'] if a['name']=='Everyday'][0]['balance_units']")" "494750"

# edit magnitude and keep direction
TXID="$(run "$SCRIPTS/transactions.py" list --search "" --limit 100 | jqv "[t for t in d['data'] if t['amount_units']==-1000][0]['id']")"
OUT="$(run "$SCRIPTS/transactions.py" edit "$TXID" --amount 15.00)"
check "edit magnitude" "$(printf '%s' "$OUT" | jqv "d['data']['amount_units']")" "-1500"
# category change flips sign when kind differs
OUT="$(run "$SCRIPTS/transactions.py" edit "$TXID" --category Salary)"
check "edit category flips to income" "$(printf '%s' "$OUT" | jqv "d['data']['amount_units']")" "1500"
OUT="$(run "$SCRIPTS/transactions.py" edit "$TXID" --category Groceries)"
check "edit category flips to expense" "$(printf '%s' "$OUT" | jqv "d['data']['amount_units']")" "-1500"
expect_fail "edit with no fields" "invalid_input" run "$SCRIPTS/transactions.py" edit "$TXID"

# delete
OUT="$(run "$SCRIPTS/transactions.py" add --account Everyday --amount 5.00 --date 2026-01-30 --direction out)"
DELID="$(printf '%s' "$OUT" | jqv "d['data']['id']")"
OUT="$(run "$SCRIPTS/transactions.py" delete "$DELID")"
check "delete one" "$(printf '%s' "$OUT" | jqv "d['data']['count']")" "1"

# transfer
OUT="$(run "$SCRIPTS/transactions.py" transfer --from Everyday --to Savings --amount 200.00 --date 2026-01-25)"
check "transfer from" "$(printf '%s' "$OUT" | jqv "d['data']['from']['name']")" "Everyday"
TGROUP="$(printf '%s' "$OUT" | jqv "d['data']['transfer_group']")"
check "transfer group length" "$(printf '%s' "$OUT" | jqv "len(d['data']['transfer_group'])")" "32"

OUT="$(run "$SCRIPTS/accounts.py" balance --as-of 2026-06-30)"
check "everyday after edit" "$(printf '%s' "$OUT" | jqv "[a for a in d['data']['accounts'] if a['name']=='Everyday'][0]['balance_units']")" "474250"
check "savings after transfer" "$(printf '%s' "$OUT" | jqv "[a for a in d['data']['accounts'] if a['name']=='Savings'][0]['balance_units']")" "120000"

# list filters
OUT="$(run "$SCRIPTS/transactions.py" list --account Everyday)"
check "list by account" "$(printf '%s' "$OUT" | jqv "len(d['data'])")" "4"
OUT="$(run "$SCRIPTS/transactions.py" list --from 2026-01-01 --to 2026-01-18)"
check "list date range" "$(printf '%s' "$OUT" | jqv "len(d['data'])")" "2"
OUT="$(run "$SCRIPTS/transactions.py" list --search Acme)"
check "list search" "$(printf '%s' "$OUT" | jqv "d['data'][0]['payee']")" "Acme"
OUT="$(run "$SCRIPTS/transactions.py" list --category Groceries)"
check "list by category" "$(printf '%s' "$OUT" | jqv "len(d['data'])")" "2"
OUT="$(run "$SCRIPTS/transactions.py" list --limit 1)"
check "list limit" "$(printf '%s' "$OUT" | jqv "len(d['data'])")" "1"

# forbidden edits / same-account transfer
TRANSFER_TX="$(run "$SCRIPTS/transactions.py" list --account Everyday | jqv "[t for t in d['data'] if t['transfer_group']][0]['id']")"
expect_fail "edit transfer rejected" "invalid_input" run "$SCRIPTS/transactions.py" edit "$TRANSFER_TX" --note x
expect_fail "same-account transfer" "invalid_input" run "$SCRIPTS/transactions.py" transfer --from Everyday --to Everyday --amount 1 --date 2026-01-01
expect_fail "negative amount" "invalid_amount" run "$SCRIPTS/transactions.py" add --account Everyday --amount -5 --date 2026-01-01 --direction out
expect_fail "missing direction" "missing_direction" run "$SCRIPTS/transactions.py" add --account Everyday --amount 5 --date 2026-01-01
expect_fail "unknown category" "not_found" run "$SCRIPTS/transactions.py" add --account Everyday --amount 5 --date 2026-01-01 --category Nope

# ===========================================================================
section "reports"
OUT="$(run "$SCRIPTS/reports.py" summary --month 2026-01)"
check "summary income" "$(printf '%s' "$OUT" | jqv "d['data']['income_units']")" "500000"
check "summary expenses" "$(printf '%s' "$OUT" | jqv "d['data']['expenses_units']")" "5750"
check "summary net" "$(printf '%s' "$OUT" | jqv "d['data']['net_units']")" "494250"
check "summary transfers excluded" "$(printf '%s' "$OUT" | jqv "d['data']['transfer_count']")" "2"

OUT="$(run "$SCRIPTS/reports.py" categories --month 2026-01)"
check "categories has Groceries" "$(printf '%s' "$OUT" | jqv "'Groceries' in [c['category'] for c in d['data']['categories']]")" "True"
check "categories groceries amount" "$(printf '%s' "$OUT" | jqv "[c for c in d['data']['categories'] if c['category']=='Groceries'][0]['amount_units']")" "5750"
OUT="$(run "$SCRIPTS/reports.py" categories --month 2026-01 --kind income)"
check "categories kind filter" "$(printf '%s' "$OUT" | jqv "'Salary' in [c['category'] for c in d['data']['categories']]")" "True"

OUT="$(run "$SCRIPTS/reports.py" cashflow --from 2026-01-01 --to 2026-02-28 --group-by month)"
check "cashflow months" "$(printf '%s' "$OUT" | jqv "len(d['data']['periods'])")" "1"
OUT="$(run "$SCRIPTS/reports.py" cashflow --from 2026-01-01 --to 2026-01-31 --group-by week)"
check "cashflow weeks" "$(printf '%s' "$OUT" | jqv "len(d['data']['periods'])")" "2"

OUT="$(run "$SCRIPTS/reports.py" networth --as-of 2026-06-30 --by-account)"
check "networth assets" "$(printf '%s' "$OUT" | jqv "d['data']['assets_units']")" "594250"
check "networth liabilities" "$(printf '%s' "$OUT" | jqv "d['data']['liabilities_units']")" "-50000"
check "networth total" "$(printf '%s' "$OUT" | jqv "d['data']['net_worth_units']")" "544250"
check "networth by-account" "$(printf '%s' "$OUT" | jqv "len(d['data']['accounts'])")" "3"
check "networth archived excluded" "$(printf '%s' "$OUT" | jqv "'Card' in [a['name'] for a in d['data']['accounts']]")" "False"

# ===========================================================================
section "budgets"
run "$SCRIPTS/budgets.py" set --category Groceries --month 2026-01 --amount 300.00 >/dev/null
OUT="$(run "$SCRIPTS/budgets.py" set --category Groceries --month 2026-01 --amount 350.00)"
check "budget upsert" "$(printf '%s' "$OUT" | jqv "d['data']['amount_units']")" "35000"
run "$SCRIPTS/budgets.py" set --category Groceries --month 2026-02 --amount 300.00 >/dev/null
OUT="$(run "$SCRIPTS/budgets.py" list --month 2026-01)"
check "budget list month" "$(printf '%s' "$OUT" | jqv "len(d['data'])")" "1"
OUT="$(run "$SCRIPTS/budgets.py" status --month 2026-01)"
check "budget spent" "$(printf '%s' "$OUT" | jqv "d['data']['budgets'][0]['spent_units']")" "5750"
check "budget remaining" "$(printf '%s' "$OUT" | jqv "d['data']['budgets'][0]['remaining_units']")" "29250"
check "budget over flag" "$(printf '%s' "$OUT" | jqv "d['data']['budgets'][0]['over_budget']")" "False"
expect_fail "budget on income category" "invalid_category" run "$SCRIPTS/budgets.py" set --category Salary --month 2026-01 --amount 10
out_bad_month="$("$PY" "$SCRIPTS/budgets.py" set --category Groceries --month 2026-13 --amount 10 2>/dev/null)"; rc=$?
[ "$rc" = "1" ] && pass || fail "bad month should exit 1 (got $rc)"
check "bad month code" "$(printf '%s' "$out_bad_month" | jqv "d['error']['code']")" "invalid_date"

# ===========================================================================
section "csv (main db)"
CSV="$TMPDIR_SMOKE/everyday.csv"
OUT="$(run "$SCRIPTS/csv_io.py" export --account Everyday --output "$CSV")"
check "export rows" "$(printf '%s' "$OUT" | jqv "d['data']['rows']")" "4"
check "export path" "$(printf '%s' "$OUT" | jqv "d['data']['path'] == '$CSV'")" "True"
check "export columns" "$(printf '%s' "$OUT" | jqv "d['data']['columns'][0]")" "date"

OUT="$(run "$SCRIPTS/csv_io.py" export --from 2026-01-01 --to 2026-01-18)"
check "export filtered" "$(printf '%s' "$OUT" | jqv "d['data']['rows']")" "2"

OUT="$(run "$SCRIPTS/csv_io.py" import "$CSV" --account Everyday)"
check "import duplicate skipped" "$(printf '%s' "$OUT" | jqv "d['data']['imported_count']")" "0"
check "import skipped count" "$(printf '%s' "$OUT" | jqv "d['data']['skipped_count']")" "4"

OUT="$(run "$SCRIPTS/csv_io.py" import "$CSV" --account Savings --dry-run)"
check "import dry-run count" "$(printf '%s' "$OUT" | jqv "d['data']['imported_count']")" "4"
check "import dry-run flag" "$(printf '%s' "$OUT" | jqv "d['data']['dry_run']")" "True"
OUT="$(run "$SCRIPTS/accounts.py" balance --as-of 2026-06-30)"
check "dry-run wrote nothing" "$(printf '%s' "$OUT" | jqv "[a for a in d['data']['accounts'] if a['name']=='Savings'][0]['balance_units']")" "120000"

run "$SCRIPTS/accounts.py" add --name Import --type checking >/dev/null
CUSTOM="$TMPDIR_SMOKE/custom.csv"
printf 'Date;Description;Value;Category\n03/02/2026;Cafe;-12.50;Dining\n04/02/2026;Bookstore;-30.00;Shopping\n' > "$CUSTOM"
OUT="$(run "$SCRIPTS/csv_io.py" import "$CUSTOM" --account Import --delimiter ';' --date-format '%d/%m/%Y' --mapping '{"date":"Date","amount":"Value","payee":"Description","category":"Category"}')"
check "custom import count" "$(printf '%s' "$OUT" | jqv "d['data']['imported_count']")" "2"
check "custom import amount" "$(printf '%s' "$OUT" | jqv "d['data']['imported'][0]['amount_units']")" "-1250"

OUT="$(run "$SCRIPTS/csv_io.py" import "$CUSTOM" --account Import --delimiter ';' --date-format '%d/%m/%Y' --mapping '{"date":"Date","amount":"Value","payee":"Description","category":"Category"}')"
check "custom reimport skipped" "$(printf '%s' "$OUT" | jqv "d['data']['skipped_count']")" "2"
OUT="$(run "$SCRIPTS/csv_io.py" import "$CUSTOM" --account Import --delimiter ';' --date-format '%d/%m/%Y' --mapping '{"date":"Date","amount":"Value","payee":"Description","category":"Category"}' --force)"
check "force imports duplicates" "$(printf '%s' "$OUT" | jqv "d['data']['imported_count']")" "2"

BADCSV="$TMPDIR_SMOKE/bad.csv"
printf 'date,amount\n2026-03-01,10.00\nnot-a-date,5.00\n' > "$BADCSV"
expect_fail "invalid rows abort" "invalid_rows" run "$SCRIPTS/csv_io.py" import "$BADCSV" --account Import
OUT="$(run "$SCRIPTS/csv_io.py" import "$BADCSV" --account Import --force)"
check "force skips invalid rows" "$(printf '%s' "$OUT" | jqv "d['data']['imported_count']")" "1"
check "force reports errors" "$(printf '%s' "$OUT" | jqv "d['data']['error_count']")" "1"

NESTED="$TMPDIR_SMOKE/nested/dir/out.csv"
run "$SCRIPTS/csv_io.py" export --output "$NESTED" >/dev/null
[ -f "$NESTED" ] && pass || fail "export did not create nested path"

# missing date/amount columns
NODATE="$TMPDIR_SMOKE/nodate.csv"
printf 'when,value\n2026-03-01,10\n' > "$NODATE"
expect_fail "missing mapped column" "invalid_input" run "$SCRIPTS/csv_io.py" import "$NODATE" --account Import --date-column nope

# ===========================================================================
section "recurring (dedicated db)"
export PERSONAL_FINANCE_DB="$TMPDIR_SMOKE/rec.db"
run "$SCRIPTS/init.py" init --currency IDR >/dev/null
run "$SCRIPTS/accounts.py" add --name Everyday --type checking >/dev/null
OUT="$(run "$SCRIPTS/recurring.py" add --name Internet --account Everyday --amount 30 --frequency monthly --start 2026-01-05 --category Utilities)"
check "recurring add next_due" "$(printf '%s' "$OUT" | jqv "d['data']['next_due']")" "2026-01-05"
check "recurring add direction" "$(printf '%s' "$OUT" | jqv "d['data']['direction']")" "out"
check "recurring add active" "$(printf '%s' "$OUT" | jqv "d['data']['active']")" "True"

OUT="$(run "$SCRIPTS/recurring.py" due --as-of 2026-01-01 --within 14)"
check "recurring due read_only" "$(printf '%s' "$OUT" | jqv "d['data']['read_only']")" "True"
check "recurring due count" "$(printf '%s' "$OUT" | jqv "d['data']['count']")" "1"

OUT="$(run "$SCRIPTS/recurring.py" run --until 2026-03-31)"
check "recurring first run" "$(printf '%s' "$OUT" | jqv "d['data']['posted_count']")" "3"
check "recurring advanced" "$(printf '%s' "$OUT" | jqv "d['data']['advanced'][0]['next_due']")" "2026-04-05"
OUT="$(run "$SCRIPTS/recurring.py" run --until 2026-03-31)"
check "recurring rerun idempotent" "$(printf '%s' "$OUT" | jqv "d['data']['posted_count']")" "0"
OUT="$(run "$SCRIPTS/accounts.py" balance --as-of 2026-12-31)"
check "recurring balance" "$(printf '%s' "$OUT" | jqv "d['data']['total_units']")" "-9000"

OUT="$(run "$SCRIPTS/recurring.py" run --until 2026-06-30 --dry-run)"
check "recurring dry-run planned" "$(printf '%s' "$OUT" | jqv "d['data']['planned_count']")" "3"
check "recurring dry-run posted" "$(printf '%s' "$OUT" | jqv "d['data']['posted_count']")" "0"
OUT="$(run "$SCRIPTS/recurring.py" list)"
check "dry-run did not advance" "$(printf '%s' "$OUT" | jqv "d['data'][0]['next_due']")" "2026-04-05"

OUT="$(run "$SCRIPTS/recurring.py" due --as-of 2026-03-20 --within 30)"
check "recurring next due" "$(printf '%s' "$OUT" | jqv "d['data']['instances'][0]['due_date']")" "2026-04-05"
check "recurring overdue false" "$(printf '%s' "$OUT" | jqv "d['data']['instances'][0]['overdue']")" "False"

OUT="$(run "$SCRIPTS/recurring.py" pause Internet)"
check "recurring pause" "$(printf '%s' "$OUT" | jqv "d['data']['active']")" "False"
OUT="$(run "$SCRIPTS/recurring.py" list)"
check "paused hidden" "$(printf '%s' "$OUT" | jqv "len(d['data'])")" "0"
OUT="$(run "$SCRIPTS/recurring.py" list --include-inactive)"
check "inactive listed" "$(printf '%s' "$OUT" | jqv "len(d['data'])")" "1"
run "$SCRIPTS/recurring.py" resume Internet >/dev/null
OUT="$(run "$SCRIPTS/recurring.py" list)"
check "resumed active" "$(printf '%s' "$OUT" | jqv "d['data'][0]['active']")" "True"

# bounded daily rule, then delete it
run "$SCRIPTS/recurring.py" add --name Gym --account Everyday --amount 5 --frequency daily --start 2026-01-01 --end 2026-01-03 --category Health >/dev/null
OUT="$(run "$SCRIPTS/recurring.py" run --until 2026-01-31)"
check "bounded rule posts 3" "$(printf '%s' "$OUT" | jqv "d['data']['posted_count']")" "3"
OUT="$(run "$SCRIPTS/recurring.py" delete Gym)"
check "recurring delete log rows" "$(printf '%s' "$OUT" | jqv "d['data']['log_rows_removed']")" "3"
expect_fail "unknown recurring" "not_found" run "$SCRIPTS/recurring.py" pause Nope
expect_fail "end before start" "invalid_input" run "$SCRIPTS/recurring.py" add --name Bad --account Everyday --amount 1 --frequency daily --start 2026-02-01 --end 2026-01-01

# ===========================================================================
section "scheduling artifacts"
for TARGET in generic claude codex nanobot crontab systemd launchd; do
  OUT="$(run "$SCRIPTS/recurring.py" schedule-hint --target "$TARGET")"
  check "hint $TARGET ok" "$(printf '%s' "$OUT" | jqv "d['ok']")" "True"
  check "hint $TARGET installs nothing" "$(printf '%s' "$OUT" | jqv "d['data']['installs_nothing']")" "True"
done

OUT="$(run "$SCRIPTS/recurring.py" schedule-hint --target generic)"
check "generic needs instructions" "$(printf '%s' "$OUT" | jqv "len(d['data']['instructions']) > 0")" "True"
check "generic has two commands" "$(printf '%s' "$OUT" | jqv "len(d['data']['commands'])")" "2"

OUT="$(run "$SCRIPTS/recurring.py" schedule-hint --target generic --path-mode relative)"
check "relative path mode" "$(printf '%s' "$OUT" | jqv "d['data']['commands'][0]['command'].startswith('python3 scripts/recurring.py')")" "True"
OUT="$(run "$SCRIPTS/recurring.py" schedule-hint --target generic --path-mode skill)"
check "skill path mode" "$(printf '%s' "$OUT" | jqv "'{skillDir}/scripts/recurring.py' in d['data']['commands'][0]['command']")" "True"
OUT="$(run "$SCRIPTS/recurring.py" schedule-hint --target crontab --path-mode abs)"
check "abs path mode" "$(printf '%s' "$OUT" | jqv "'$SCRIPTS/recurring.py' in d['data']['commands'][0]['command']")" "True"

OUT="$(run "$SCRIPTS/recurring.py" schedule-hint --target nanobot)"
check "nanobot cron_expr" "$(printf '%s' "$OUT" | jqv "d['data']['cron_expr']")" "0 8 * * *"
check "nanobot message invocation" "$(printf '%s' "$OUT" | jqv "'\$personal-finance' in d['data']['message']")" "True"
check "nanobot tz string" "$(printf '%s' "$OUT" | jqv "isinstance(d['data']['tz'], str)")" "True"
check "nanobot cron_tool action" "$(printf '%s' "$OUT" | jqv "d['data']['cron_tool']['action']")" "add"
check "nanobot at-time" "$(printf '%s' "$OUT" | jqv "d['data']['cron_expr']")" "0 8 * * *"
OUT="$(run "$SCRIPTS/recurring.py" schedule-hint --target nanobot --at-time 21:45)"
check "nanobot custom time" "$(printf '%s' "$OUT" | jqv "d['data']['cron_expr']")" "45 21 * * *"

OUT="$(run "$SCRIPTS/recurring.py" schedule-hint --target crontab)"
printf '%s' "$OUT" | "$PY" -c 'import json,sys
d=json.load(sys.stdin)
open("'"$TMPDIR_SMOKE"'/cron.txt","w").write("\n".join(d["data"]["install"]["lines"])+"\n")'
sh -n "$TMPDIR_SMOKE/cron.txt" 2>/dev/null && pass || fail "crontab lines not shell-parseable"
CRON_LINES="$(cat "$TMPDIR_SMOKE/cron.txt")"
case "$CRON_LINES" in *"run --until"*) pass;; *) fail "crontab missing run --until";; esac
case "$CRON_LINES" in *" due "*) pass;; *) fail "crontab missing due";; esac

OUT="$(run "$SCRIPTS/recurring.py" schedule-hint --target systemd)"
check "systemd service file" "$(printf '%s' "$OUT" | jqv "'personal-finance-recurring.service' in d['data']['install']['files']")" "True"
check "systemd run --until" "$(printf '%s' "$OUT" | jqv "'run --until' in d['data']['install']['files']['personal-finance-recurring.service']")" "True"
check "systemd due" "$(printf '%s' "$OUT" | jqv "' due ' in d['data']['install']['files']['personal-finance-recurring.service']")" "True"
check "systemd escaped percent" "$(printf '%s' "$OUT" | jqv "'%%F' in d['data']['install']['files']['personal-finance-recurring.service']")" "True"

OUT="$(run "$SCRIPTS/recurring.py" schedule-hint --target launchd)"
printf '%s' "$OUT" | "$PY" -c 'import json,sys,plistlib
d=json.load(sys.stdin)
plistlib.loads(d["data"]["install"]["files"]["com.personal-finance.recurring.plist"].encode())' && pass || fail "launchd plist not valid"

# Execute the emitted run command twice; second time must post nothing.
GEN="$(run "$SCRIPTS/recurring.py" schedule-hint --target generic --path-mode abs)"
RUNCMD="$(printf '%s' "$GEN" | jqv "[c['command'] for c in d['data']['commands'] if c['step']=='post'][0]")"
eval "$RUNCMD" >/dev/null 2>&1 && pass || fail "emitted run command failed"
eval "$RUNCMD" >/dev/null 2>&1 && pass || fail "emitted run command failed on second call"
OUT="$(run "$SCRIPTS/recurring.py" run)"
check "emitted run idempotent" "$(printf '%s' "$OUT" | jqv "d['data']['posted_count']")" "0"
DUECMD="$(printf '%s' "$GEN" | jqv "[c['command'] for c in d['data']['commands'] if c['step']=='report'][0]")"
eval "$DUECMD" >/dev/null 2>&1 && pass || fail "emitted due command failed"

# ===========================================================================
section "currency coverage"
export PERSONAL_FINANCE_DB="$TMPDIR_SMOKE/jpy.db"
run "$SCRIPTS/init.py" init --currency JPY >/dev/null
run "$SCRIPTS/accounts.py" add --name Wallet --type cash >/dev/null
OUT="$(run "$SCRIPTS/transactions.py" add --account Wallet --amount 1250 --date 2026-01-10 --direction out)"
check "jpy units" "$(printf '%s' "$OUT" | jqv "d['data']['amount_units']")" "-1250"
check "jpy format" "$(printf '%s' "$OUT" | jqv "d['data']['amount']")" "JPY -1,250"
OUT="$(run "$SCRIPTS/settings.py" set --currency EUR)"
check "relabel warning" "$(printf '%s' "$OUT" | jqv "d['data'].get('warning')")" "existing amounts are relabeled, not converted"

export PERSONAL_FINANCE_DB="$TMPDIR_SMOKE/bhd.db"
run "$SCRIPTS/init.py" init --currency BHD >/dev/null
run "$SCRIPTS/accounts.py" add --name Wallet --type cash >/dev/null
OUT="$(run "$SCRIPTS/transactions.py" add --account Wallet --amount 1.500 --date 2026-01-10 --direction out)"
check "bhd units (exponent 3)" "$(printf '%s' "$OUT" | jqv "d['data']['amount_units']")" "-1500"
check "bhd format" "$(printf '%s' "$OUT" | jqv "d['data']['amount']")" "BHD -1.500"

export PERSONAL_FINANCE_DB="$TMPDIR_SMOKE/custom.db"
run "$SCRIPTS/init.py" init --currency QQX >/dev/null
OUT="$(run "$SCRIPTS/settings.py" show)"
check "custom exponent" "$(printf '%s' "$OUT" | jqv "d['data']['currency_exponent']")" "2"
check "custom format" "$(printf '%s' "$OUT" | jqv "d['data']['sample_amount']")" "QQX 1,250.00"

# env var precedence and --currency override
export PERSONAL_FINANCE_DB="$TMPDIR_SMOKE/env.db"
run "$SCRIPTS/init.py" init >/dev/null
OUT="$(run "$SCRIPTS/settings.py" show)"
check "default currency" "$(printf '%s' "$OUT" | jqv "d['data']['currency']")" "USD"
OUT="$(env PERSONAL_FINANCE_CURRENCY=EUR "$PY" "$SCRIPTS/settings.py" show)"
check "env currency" "$(printf '%s' "$OUT" | jqv "d['data']['currency']")" "EUR"
OUT="$(env PERSONAL_FINANCE_CURRENCY=EUR "$PY" "$SCRIPTS/settings.py" show --currency GBP)"
check "flag overrides env" "$(printf '%s' "$OUT" | jqv "d['data']['currency']")" "GBP"
expect_fail "invalid currency code" "invalid_currency" run "$SCRIPTS/settings.py" set --currency "12"
expect_fail "settings set without currency" "usage" run "$SCRIPTS/settings.py" set

# --db flag overrides env
OTHERDB="$TMPDIR_SMOKE/other.db"
run "$SCRIPTS/init.py" init --db "$OTHERDB" --currency CAD >/dev/null
OUT="$(run "$SCRIPTS/settings.py" show --db "$OTHERDB")"
check "--db override" "$(printf '%s' "$OUT" | jqv "d['data']['currency']")" "CAD"

# ===========================================================================
section "output formats and quiet"
export PERSONAL_FINANCE_DB="$TMPDIR_SMOKE/main.db"
OUT="$(run "$SCRIPTS/accounts.py" list --format table)"
case "$OUT" in *"name"*) pass;; *) fail "table output missing header";; esac
case "$OUT" in *'{'*) fail "table output should not be JSON";; *) pass;; esac
QUIET="$(run "$SCRIPTS/transactions.py" add --account Everyday --amount 3.00 --date 2026-01-31 --direction out --quiet)"
check "quiet suppresses output" "$QUIET" ""

# ===========================================================================
section "error handling"
export PERSONAL_FINANCE_DB="$TMPDIR_SMOKE/main.db"
expect_usage "missing required flag" run "$SCRIPTS/transactions.py" add --account Everyday --amount 10
expect_usage "unknown verb" run "$SCRIPTS/accounts.py" bogus
BADMONTH="$("$PY" "$SCRIPTS/reports.py" summary --month 2026-13 2>/dev/null)"; rc=$?
[ "$rc" = "1" ] && pass || fail "bad report month exit ($rc)"
check "bad report month code" "$(printf '%s' "$BADMONTH" | jqv "d['error']['code']")" "invalid_date"
BADRANGE="$("$PY" "$SCRIPTS/reports.py" cashflow --from 2026-02-01 --to 2026-01-01 2>/dev/null)"; rc=$?
[ "$rc" = "1" ] && pass || fail "bad range exit ($rc)"
check "bad range code" "$(printf '%s' "$BADRANGE" | jqv "d['error']['code']")" "invalid_input"
BADCSVFILE="$("$PY" "$SCRIPTS/csv_io.py" import "$TMPDIR_SMOKE/missing.csv" --account Everyday 2>/dev/null)"; rc=$?
[ "$rc" = "1" ] && pass || fail "missing csv exit ($rc)"
check "missing csv code" "$(printf '%s' "$BADCSVFILE" | jqv "d['error']['code']")" "not_found"
expect_usage "bad direction" run "$SCRIPTS/transactions.py" add --account Everyday --amount 1 --date 2026-01-01 --direction sideways

# ===========================================================================
section "portability"
LEAKS="$(grep -RIl -e 'nanobot' -e '\$personal-finance' -e 'claude' -e 'codex' \
  "$SKILL/SKILL.md" "$SKILL/references" "$SKILL/scripts" \
  | grep -v -e "$SKILL/references/scheduling.md" -e "$SKILL/scripts/recurring.py" || true)"
if [ -z "$LEAKS" ]; then pass; else fail "host tokens leaked into: $LEAKS"; fi
grep -q 'TODO' "$SKILL/SKILL.md" && fail "SKILL.md contains TODO" || pass
# skill root may contain only SKILL.md + allowed dirs
STRAY="$(find "$SKILL" -maxdepth 1 -mindepth 1 ! -name SKILL.md ! -name scripts ! -name references ! -name assets)"
[ -z "$STRAY" ] && pass || fail "stray files in skill root: $STRAY"
# _lib.py must not be executable
[ -x "$SCRIPTS/_lib.py" ] && fail "_lib.py should not be executable" || pass
for script in init settings accounts categories transactions budgets recurring reports csv_io; do
  [ -x "$SCRIPTS/$script.py" ] && pass || fail "$script.py is not executable"
done

# ===========================================================================
printf '\n========================================\n'
printf 'passed: %d   failed: %d\n' "$PASS" "$FAIL"
if [ "$FAIL" -ne 0 ]; then exit 1; fi
echo "smoke test OK"
