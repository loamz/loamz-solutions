# Agents that use the solutions

`loamz-solutions serve` exposes the Mail Concierge and the Paper Trader as tools on a Loamz deployment, so an agent can
answer "what needs me today?" or "why did you buy AAPL?", and act (carefully) on request.

```bash
export SOLUTIONS_TOKEN=$(openssl rand -hex 24)
uv run loamz-solutions serve --demo --port 8765        # a synthetic inbox and a simulated market; nothing real is touched
uv run loamz-solutions serve --print-config --url http://host.docker.internal:8765/sse    # the entry for your deployment
```

Real accounts: leave out `--demo` and set `MAIL_*` (see `.env.example`) and/or `ALPACA_*` + `TRADE_SYMBOLS`; each solution is
offered only when configured.

## Connect it to a deployment
Paste the printed JSON into `TOOL_SERVERS` (Compose `.env`) or `LOAMZ_AGENTS_TOOLS__SERVERS`. Then give an agent the tools it
needs in `allowed_tools`, for example:

```json
{"name": "Concierge", "allowed_tools": ["solutions__mail_digest", "solutions__mail_ask", "solutions__trade_status", "solutions__trade_place_order"]}
```

## What runs freely and what asks first
| Tools | Behaviour |
| --- | --- |
| `mail_sync`, `mail_digest`, `mail_top`, `mail_ask`, `trade_status`, `trade_signals`, `trade_explain`, `trade_report`, `trade_kill` | read-only (or a safe stop): the operator vouches for them in `safe_tools`; they run at once |
| `trade_place_order`, `trade_run_once`, `trade_resume` | change something: **a person approves each call** in the console or through the approvals API, and the risk limits still apply after approval |

Live trading cannot be reached through the tool server at all: it carries no human approver, and a live account's every order
needs one. Paper first.

## Safety, in layers
1. The agent only has the tools named in its `allowed_tools`.
2. Tools that change anything wait for a person.
3. The trader's guard still applies to an approved order: allowlist, notional, position, daily loss, hours, order count,
   cooldown, kill switch.
4. Everything is in the hash-chained ledger; `trade_explain` and `trade_report` read it back.
