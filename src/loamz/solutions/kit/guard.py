"""The guard: rules that stand between a decision and an action.

A solution proposes an `Action` (place an order, archive a message, send a reply). The `Guard` asks every rule, in order.
A rule answers allow, deny, or "needs approval". Denied actions never run; actions that need approval run only when the
approver says yes. Everything is written to the ledger: proposed, then denied or approved, then done or failed.

    guard = Guard(ledger, [MaxPerAction("notional", 500), KillSwitch(".state/KILL")], approver=ask_me)
    result = guard.run(Action("order", "AAPL", {"side": "buy", "qty": 2, "notional": 380}), execute=broker.submit)

Rules are small objects with `check(action, ledger) -> Verdict`, so a solution adds its own (trading hours, daily loss).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from loamz.solutions.kit.ledger import Ledger


class Decision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    APPROVE = "needs_approval"


@dataclass(frozen=True)
class Verdict:
    decision: Decision
    reason: str = ""

    @staticmethod
    def allow() -> Verdict:
        return Verdict(Decision.ALLOW)

    @staticmethod
    def deny(reason: str) -> Verdict:
        return Verdict(Decision.DENY, reason)

    @staticmethod
    def approve(reason: str) -> Verdict:
        return Verdict(Decision.APPROVE, reason)


@dataclass(frozen=True)
class Action:
    kind: str  # "order", "archive", "reply" ...
    subject: str  # what it is about: a symbol, a message id
    params: dict[str, Any] = field(default_factory=dict)


class Rule(Protocol):
    name: str

    def check(self, action: Action, ledger: Ledger) -> Verdict: ...


Approver = Callable[[Action, str], bool]
"""Asked when a rule wants a human: returns True to proceed. The default refuses, so nothing runs unattended by accident."""


@dataclass
class Outcome:
    status: str  # "done", "denied", "refused", "failed"
    reason: str = ""
    result: Any = None


def refuse(action: Action, reason: str) -> bool:
    return False


class Guard:
    def __init__(self, ledger: Ledger, rules: list[Rule], approver: Approver = refuse) -> None:
        self.ledger, self.rules, self.approver = ledger, rules, approver

    def run(self, action: Action, execute: Callable[[Action], Any]) -> Outcome:
        subject = f"{action.kind}:{action.subject}"
        self.ledger.append("action.proposed", subject, **action.params)
        needs_approval: list[str] = []
        for rule in self.rules:
            verdict = rule.check(action, self.ledger)
            if verdict.decision is Decision.DENY:
                self.ledger.append("action.denied", subject, rule=rule.name, reason=verdict.reason)
                return Outcome("denied", f"{rule.name}: {verdict.reason}")
            if verdict.decision is Decision.APPROVE:
                needs_approval.append(f"{rule.name}: {verdict.reason}")
        if needs_approval:
            reason = "; ".join(needs_approval)
            if not self.approver(action, reason):
                self.ledger.append("action.refused", subject, reason=reason)
                return Outcome("refused", reason)
            self.ledger.append("action.approved", subject, reason=reason)
        try:
            result = execute(action)
        except Exception as error:  # noqa: BLE001 - recorded, then reported to the caller
            self.ledger.append("action.failed", subject, error=f"{type(error).__name__}: {error}")
            return Outcome("failed", str(error))
        self.ledger.append(
            "action.done",
            subject,
            **(result if isinstance(result, dict) else {"result": str(result)}),
        )
        return Outcome("done", result=result)


# ------------------------------------------------------------------ rules every solution can use
class KillSwitch:
    """Nothing runs while the file exists (or the environment variable is set). `touch .state/KILL` stops it at once."""

    name = "kill-switch"

    def __init__(self, path: str | Path = ".state/KILL", env: str = "LOAMZ_SOLUTIONS_KILL") -> None:
        self.path, self.env = Path(path), env

    def check(self, action: Action, ledger: Ledger) -> Verdict:
        if self.path.exists() or os.environ.get(self.env):
            return Verdict.deny("the kill switch is on")
        return Verdict.allow()


class MaxPerAction:
    """No single action may exceed `limit` of a numeric parameter (an order's notional, a batch's size)."""

    def __init__(self, param: str, limit: float, kinds: tuple[str, ...] | None = None) -> None:
        self.param, self.limit, self.kinds = param, limit, kinds
        self.name = f"max-{param}"

    def check(self, action: Action, ledger: Ledger) -> Verdict:
        if self.kinds and action.kind not in self.kinds:
            return Verdict.allow()
        value = float(action.params.get(self.param, 0) or 0)
        if value > self.limit:
            return Verdict.deny(f"{self.param} {value:g} is above the limit {self.limit:g}")
        return Verdict.allow()


class Allowlist:
    """Only these subjects (symbols, folders, senders) may be acted on."""

    def __init__(self, allowed: set[str], kinds: tuple[str, ...] | None = None) -> None:
        self.allowed, self.kinds, self.name = {a.upper() for a in allowed}, kinds, "allowlist"

    def check(self, action: Action, ledger: Ledger) -> Verdict:
        if self.kinds and action.kind not in self.kinds:
            return Verdict.allow()
        if action.subject.upper() not in self.allowed:
            return Verdict.deny(f"{action.subject} is not on the allowlist")
        return Verdict.allow()


class RequireApproval:
    """Every action of these kinds waits for a human."""

    def __init__(self, kinds: tuple[str, ...], reason: str = "a person must confirm this") -> None:
        self.kinds, self.reason, self.name = kinds, reason, "require-approval"

    def check(self, action: Action, ledger: Ledger) -> Verdict:
        return Verdict.approve(self.reason) if action.kind in self.kinds else Verdict.allow()
