# Scheduling (agent-agnostic)

Recurring rules live in the ledger database. Scheduling is opt-in and
one-directional: `recurring.py schedule-hint` **emits** an artifact for a
chosen target and installs nothing. The host or user registers it. The ledger
stays the source of truth for what repeats, when, and how much; the scheduler
only triggers.

Two commands are always involved:

1. `recurring.py run` — writes. Posts every occurrence up to a date (default
   today), idempotent through `recurring_log`, and catches up after downtime.
2. `recurring.py due` — read-only. Lists due and upcoming instances.

## Targets

| Target | Emits | Notifies in chat? |
|---|---|---|
| `generic` | instructions, exact commands, notes | depends on host |
| `nanobot` | `{name, message, cron_expr, tz}` for the nanobot cron tool | yes |
| `claude` | instructions plus commands; no native scheduler | no |
| `codex` | instructions plus commands; no native scheduler | no |
| `crontab` | ready `crontab` lines (post line plus read-only digest line) | no |
| `systemd` | `personal-finance-recurring.service` and `.timer` contents | no |
| `launchd` | `com.personal-finance.recurring.plist` contents | no |

`--path-mode abs|relative|skill` controls how the emitted command references
`scripts/recurring.py`:

- `abs` — absolute path resolved now (default for the OS targets and nanobot).
- `relative` — `scripts/recurring.py`.
- `skill` — the `{skillDir}/scripts/recurring.py` placeholder for a host that
  substitutes the loaded skill directory.

nanobot does **not** substitute `{baseDir}`-style placeholders, so its default
path mode is `abs`. Other agents that do substitute a skill-directory token
can use `--path-mode skill`.

Other flags: `--within DAYS` (report window, default 14), `--at-time HH:MM`
(daily trigger, default 08:00), `--tz ZONE` (cron timezone for the nanobot
target; default `$TZ` or `UTC`), `--name JOB` (nanobot job name).

## Agent targets

For `generic`, `claude`, and `codex` the artifact is instructions plus the
commands. Register it with whatever the host offers: a cron tool, reminders, a
task runner, or an OS scheduler. When the host has no native scheduler, use one
of the OS targets below.

### nanobot

The `nanobot` target returns fields for the built-in cron tool:

```json
{
  "name": "personal-finance-daily",
  "message": "$personal-finance Post due recurring transactions and report ...",
  "cron_expr": "0 8 * * *",
  "tz": "Asia/Jakarta"
}
```

Create the job with `action="add"` from a live chat session; a script cannot
create cron jobs, and jobs cannot be added from inside a cron run. Keep
`cron_expr` and `tz` together (the tool rejects `tz` without `cron_expr`). List
jobs to find the id for removal. Protected system jobs (`dream`, `heartbeat`)
can be listed but not removed.

## OS targets (portable fallback, no chat notification)

These run the script directly. They produce no chat message, so they are the
fallback when the host has no scheduler. All three compute the date at run
time (`run --until "$(date +%F)"`), so the artifact never goes stale, and use
`--quiet` to keep an idle run silent.

### crontab

The artifact provides two lines: the post line and, five minutes later, the
read-only digest line. Append them with `crontab -e`. In crontab the `%` in
`date +%F` is escaped as `\%`.

```
0 8 * * * python3 <script> run --until "$(date +\%F)" --db <db> --quiet
5 8 * * * python3 <script> due --within 14 --db <db> --quiet
```

### systemd (Linux, user units)

Write the two emitted files to `~/.config/systemd/user/`, then:

```
systemctl --user daemon-reload
systemctl --user enable --now personal-finance-recurring.timer
```

`Persistent=true` catches up runs missed while the machine was off. Cancel with
`systemctl --user disable --now personal-finance-recurring.timer`.

### launchd (macOS)

Write the emitted plist to `~/Library/LaunchAgents/`, then:

```
launchctl load ~/Library/LaunchAgents/com.personal-finance.recurring.plist
```

launchd runs missed jobs on wake. Cancel with `launchctl unload`.

## Rules and triggers are independent

- Removing or cancelling a scheduled trigger does not delete or pause the
  recurring rules in the ledger.
- Deleting or pausing a rule does not remove the trigger.
- Pausing a rule (`recurring.py pause`) stops postings even while a trigger
  keeps firing; `run` then posts nothing for it.
- The trigger only runs commands; it never computes amounts.

See `commands.md` for exact flags and `schema.md` for the ledger model.
