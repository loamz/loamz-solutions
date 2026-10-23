"""The ledger detects tampering; the guard denies, asks and records."""

from pathlib import Path

from loamz.solutions.kit import (
    Action,
    Allowlist,
    Guard,
    KillSwitch,
    Ledger,
    MaxPerAction,
    RequireApproval,
)


def test_the_ledger_chains_entries_and_notices_a_changed_one(tmp_path: Path):
    ledger = Ledger(tmp_path / "l.sqlite")
    for i in range(5):
        ledger.append("thing.happened", f"s{i}", n=i)
    assert ledger.verify() == {"ok": True, "checked": 5, "first_bad_id": None}
    assert [e.payload["n"] for e in ledger.entries(kind="thing.*")] == [0, 1, 2, 3, 4]
    assert [e.subject for e in ledger.entries(subject="s3")] == ["s3"]
    ledger.db.execute("UPDATE entries SET payload='{\"n\": 99}' WHERE id=3")
    assert ledger.verify()["first_bad_id"] == 3
    other = Ledger(tmp_path / "l2.sqlite")
    other.append("a")
    other.append("b")
    other.db.execute("DELETE FROM entries WHERE id=1")
    assert not other.verify()["ok"]


def test_state_survives_reopening(tmp_path: Path):
    path = tmp_path / "s.sqlite"
    first = Ledger(path)
    first.set("cursor", "abc")
    first.close()
    assert Ledger(path).get("cursor") == "abc" and Ledger(path).get("missing", "d") == "d"


def make_guard(tmp_path: Path, approve=False, extra=()):
    ledger = Ledger(tmp_path / "g.sqlite")
    rules = [
        KillSwitch(tmp_path / "KILL"),
        MaxPerAction("notional", 500),
        Allowlist({"AAPL", "MSFT"}),
        *extra,
    ]
    return Guard(ledger, rules, approver=lambda action, reason: approve), ledger


def order(symbol="AAPL", notional=100):
    return Action("order", symbol, {"side": "buy", "notional": notional})


def kinds(ledger):
    return [e.kind for e in ledger.entries()]


def test_an_allowed_action_runs_and_is_recorded(tmp_path):
    guard, ledger = make_guard(tmp_path)
    outcome = guard.run(order(), lambda action: {"filled": 1})
    assert outcome.status == "done" and outcome.result == {"filled": 1}
    assert kinds(ledger) == ["action.proposed", "action.done"]


def test_a_rule_can_deny_and_the_action_never_runs(tmp_path):
    guard, ledger = make_guard(tmp_path)
    ran = []
    for action, why in ((order(notional=900), "above the limit"), (order("TSLA"), "allowlist")):
        outcome = guard.run(action, lambda a: ran.append(a))
        assert outcome.status == "denied" and why in outcome.reason
    assert ran == [] and kinds(ledger).count("action.denied") == 2


def test_the_kill_switch_stops_everything_at_once(tmp_path):
    guard, _ = make_guard(tmp_path)
    (tmp_path / "KILL").write_text("")
    assert guard.run(order(), lambda a: None).status == "denied"
    (tmp_path / "KILL").unlink()
    assert guard.run(order(), lambda a: {}).status == "done"


def test_actions_that_need_approval_wait_for_a_yes(tmp_path):
    needs = RequireApproval(("order",), "live trading")
    refused, ledger = make_guard(tmp_path, approve=False, extra=(needs,))
    assert refused.run(order(), lambda a: {}).status == "refused"
    assert "action.refused" in kinds(ledger)
    approved, _ = make_guard(tmp_path / "approved", approve=True, extra=(needs,))
    assert approved.run(order(), lambda a: {}).status == "done"
    assert "action.approved" in kinds(approved.ledger)


def test_a_failing_action_is_recorded_not_raised(tmp_path):
    guard, ledger = make_guard(tmp_path)

    def boom(action):
        raise RuntimeError("broker down")

    outcome = guard.run(order(), boom)
    assert outcome.status == "failed" and kinds(ledger)[-1] == "action.failed"


def test_approval_above_a_limit_only_asks_about_the_big_ones(tmp_path):
    from loamz.solutions.kit import RequireApprovalAbove

    rule = RequireApprovalAbove("amount", 1000, kinds=("pay",))
    ledger = Ledger()
    assert rule.check(Action("pay", "x", {"amount": 900}), ledger).decision.value == "allow"
    assert (
        rule.check(Action("pay", "x", {"amount": 1500}), ledger).decision.value == "needs_approval"
    )
    assert rule.check(Action("refund", "x", {"amount": 99999}), ledger).decision.value == "allow"
