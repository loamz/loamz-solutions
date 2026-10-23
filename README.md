# loamz-solutions

Solutions built on [Loamz](https://github.com/loamz/loamz-server), and the small kit they share. Each one is useful on its
own, from a terminal, and each is also offered to agents on a Loamz deployment as tools, with a person approving anything that
changes something.

| Solution | What it does | Try it (no credentials) |
| --- | --- | --- |
| **Mail Concierge** ([docs](docs/mail.md)) | Reads a mailbox read-only, ranks every message (urgent, important, normal, low, noise) with rules first and a model only for the rest, writes a daily digest, answers "what did X say about Y" | `loamz-solutions mail demo` |
| **Paper Trader** ([docs](docs/trading.md)) | A pluggable signal model, a guard of risk rules (allowlist, size, position, daily loss, hours, cooldown, kill switch), Alpaca paper or a simulator, a hash-chained ledger with P&L, a backtester that fills at the next open | `loamz-solutions trade demo` |
| **Data Concierge** ([docs](docs/data.md)) | Maps where your data lives (folders, mail, Loamz), searches across all of it, answers questions with citations, suggests what to do with it | `loamz-solutions data demo` |
| **Agent tools** ([docs](docs/agents.md)) | `loamz-solutions serve` offers all of the above to agents over MCP; reads run freely, orders wait for approval | `loamz-solutions serve --demo` |

```bash
uv sync
uv run loamz-solutions --help
```

## The kit
`loamz.solutions.kit` is what the solutions have in common, and what you use to build the next one:
- `Record` and `Connector`: read from anywhere into one shape.
- `Ledger`: an append-only, hash-chained record of what was decided and done, plus small state. Tamper with a line and
  `verify()` names it.
- `Guard` and rules: stand between a decision and an action. Any rule can deny; some can demand a person. Everything is
  recorded: proposed, denied or approved, done or failed.
- `LLM`: a tiny client for any OpenAI-compatible endpoint.

A new solution is: connectors in, a decision (rules, a model), a guarded action out, a ledger line for each step, and a tool
wrapper so agents can use it.

Credentials live in a local `.env` (see `.env.example`), never in the repository. Real-money trading is refused by default.

## Run as a service
```bash
export SOLUTIONS_TOKEN=$(openssl rand -hex 24)
docker compose up -d            # or: uv run loamz-solutions serve
uv run loamz-solutions serve --print-config    # the entry to paste into the cell's TOOL_SERVERS
```

Apache-2.0.
