# loamz-solutions

Solutions built on Loamz, and the small kit they share.

| Solution | What it does |
| --- | --- |
| `loamz.solutions.mail` | Mail Concierge: reads a mailbox over IMAP, ranks every message by priority, keeps a searchable index, writes a daily digest, answers questions about your mail |
| `loamz.solutions.trading` | Paper Trader: a pluggable signal model, a risk guard, an order broker (Alpaca paper or a simulator), an append-only ledger with P&L, and a backtester |
| `loamz.solutions.concierge` | Data Concierge: an inventory of where your data lives, and questions across it |

The kit (`loamz.solutions.kit`) is what they have in common: `Record` and `Connector` (read from anywhere), `Ledger` (an
append-only, hash-chained record of what was decided and done), `Guard` (rules that stand between a decision and an
action, with a kill switch and human approval), and an OpenAI-compatible `LLM` client.

```bash
uv sync
uv run loamz-solutions --help
```

Credentials live in a local `.env` (see `.env.example`), never in the repository.
