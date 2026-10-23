# Build your own solution

A solution is five small things. The three shipped ones, and `examples/invoice_approver.py` (about sixty lines, tested), are
all built this way.

| Piece | In the kit | You write |
| --- | --- | --- |
| Read | `Connector` yields `Record`s (an inbox, an API, a folder, a database) | `fetch(since, limit)` |
| Decide | rules, a model (`LLM`), or both: rules first, the model for what they cannot settle | a function returning a verdict |
| Gate | `Guard` with `Allowlist`, `MaxPerAction`, `KillSwitch`, `RequireApproval`, `RequireApprovalAbove`, or your own rule (`check(action, ledger) -> Verdict`) | the limits, in one place |
| Act | `guard.run(Action(kind, subject, params), execute=fn)` | `fn`, the one function that touches the world |
| Remember and tell | `Ledger` (hash-chained, append-only) and `Notifier` (Slack, Discord, ntfy, email, console; once per event) | what is worth saying |

```python
from loamz.solutions.kit import (
    Action,
    Allowlist,
    Guard,
    KillSwitch,
    Ledger,
    MaxPerAction,
    RequireApprovalAbove,
)

ledger = Ledger(".state/mine.sqlite")
guard = Guard(
    ledger,
    [
        KillSwitch(),
        Allowlist({"ACME"}),
        MaxPerAction("amount", 5000),
        RequireApprovalAbove("amount", 1000),
    ],
    approver=lambda action, reason: (
        input(f"{action.subject} {action.params}: {reason}? [y/N] ") == "y"
    ),
)
guard.run(
    Action("pay", "ACME", {"amount": 1200}),
    execute=lambda a: bank.pay(a.subject, a.params["amount"]),
)
```

Every proposal, denial, approval, failure and success is in the ledger, and `ledger.verify()` names the first line anyone
altered.

## Offer it to agents
Wrap the read-only parts and the guarded action as MCP tools (see `loamz/solutions/server.py`: a method per tool, one lock, plain
text results). Tools that only read go in `safe_tools`; anything that changes something is left out, so the platform asks a person
before each call. The agent never sees credentials and cannot get past the Guard.

## Habits that keep a solution safe
1. The model proposes, rules decide. A model that fails or answers nonsense means "do nothing".
2. One function acts. Everything else is reading and recording.
3. An idempotent action id (derive it from what, which, and the day) so running twice cannot do it twice.
4. Test the rules, not the model: a table of cases with the reason each must be denied.
5. Start on paper (a simulator, a sandbox account, a dry run) and read the ledger weekly before widening any limit.
