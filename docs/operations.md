# Running it day to day

## One command
```bash
uv run loamz-solutions run            # everything that is configured, on a schedule
uv run loamz-solutions run --demo     # the same on the synthetic inbox, simulated market and sample files
```

| Job | When | Needs |
| --- | --- | --- |
| mail sync (and an alert for each new urgent message) | every `MAIL_EVERY` seconds (600) | `MAIL_*` |
| mail digest, pushed as an alert | daily at `DIGEST_AT` (07:30, local time) | `MAIL_*` |
| trading pass (only while the market is open) | every `TRADE_EVERY` seconds (300) | `ALPACA_*`, `TRADE_SYMBOLS` |
| data rescan | every `DATA_EVERY` seconds (3600) | `DATA_FOLDERS` |

A job that fails is written to the scheduler's ledger and reported once an hour at most; the others carry on. `trade kill`
stops trading without stopping the rest. Under systemd, or as a container (`docker compose up -d`), restart it on failure:
state is on disk and alerts are never repeated after a restart.

## Alerts
Set one or more of these; with none, alerts print to the console.

| Setting | Delivers to |
| --- | --- |
| `NOTIFY_WEBHOOK_URL` | Slack, Discord or ntfy (detected from the URL), or any endpoint that accepts JSON `{key, title, body, level}` |
| `NOTIFY_EMAIL_TO` (+ `SMTP_HOST`, `MAIL_USER`, `MAIL_PASSWORD`) | an email to that address |

What triggers one: an urgent message arrives; the trader's model signals buy or sell and the order was **traded**, **denied** (with the
rule that stopped it), **refused** or **failed**; the broker is unreachable; a job fails; the morning digest. Each event is
announced once. To be told about signals without trading at all, run with the model and set `TRADE_MAX_ORDERS_PER_DAY=0`: every
signal is then reported as denied, with the model's reason.

## The dashboard
```bash
uv run loamz-solutions dashboard --port 8780      # http://127.0.0.1:8780, refreshes every minute
```
A read-only page: mail that needs you, the trader's equity, positions, equity curve, recent decisions and kill-switch state, the
last alerts, and the map of your data. On an address other than localhost it needs `DASHBOARD_TOKEN` (`?token=…` once, then a
cookie).

## What lives where
`.state/` (or `STATE_DIR`): `mail/` (index and ledger), `trader/` (ledger; `KILL` is the kill switch), `data/` (index), `run/`
(scheduler ledger with every alert sent). Back it up like any other data; delete a folder to start that solution afresh.
