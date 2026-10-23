from datetime import datetime, timedelta

from loamz.solutions.kit import Ledger
from loamz.solutions.runner import Job, Scheduler, jobs_for


class Box:
    def __init__(self):
        self.sent = []

    def send(self, alert):
        self.sent.append(alert)


T = datetime(2026, 10, 24, 6, 0)


def test_interval_jobs_run_when_due_and_not_before():
    calls = []
    s = Scheduler(
        [Job("sync", lambda: calls.append(1), every=timedelta(minutes=10))], Ledger(), Box()
    )
    assert s.tick(T) == ["sync"]
    assert s.tick(T + timedelta(minutes=9)) == []
    assert s.tick(T + timedelta(minutes=10)) == ["sync"]
    assert len(calls) == 2


def test_a_daily_job_runs_once_a_day_after_its_time():
    s = Scheduler([Job("digest", lambda: None, daily_at=(7, 30))], Ledger(), Box())
    assert s.tick(T.replace(hour=7, minute=0)) == []
    assert s.tick(T.replace(hour=7, minute=30)) == ["digest"]
    assert s.tick(T.replace(hour=9)) == []  # already done today
    assert s.tick(T.replace(hour=7, minute=31) + timedelta(days=1)) == ["digest"]


def test_a_started_late_daily_job_still_runs_that_day():
    s = Scheduler([Job("digest", lambda: None, daily_at=(7, 30))], Ledger(), Box())
    assert s.tick(T.replace(hour=12)) == ["digest"]


def test_a_failing_job_is_recorded_and_reported_and_the_others_carry_on():
    ledger, box, ran = Ledger(), Box(), []

    def boom():
        raise RuntimeError("imap down")

    s = Scheduler(
        [
            Job("mail", boom, every=timedelta(minutes=1)),
            Job("data", lambda: ran.append(1), every=timedelta(minutes=1)),
        ],
        ledger,
        box,
    )
    assert s.tick(T) == ["data"]
    assert (
        ran == [1]
        and ledger.entries(kind="job.error")[0].payload["error"] == "RuntimeError: imap down"
    )
    assert box.sent and box.sent[0].title == "mail failed"


def test_the_jobs_for_a_demo_service_all_run_and_alert_about_urgent_mail(tmp_path, monkeypatch):
    from loamz.solutions import server

    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    service = server.from_env(demo=True)
    box = Box()
    jobs = jobs_for(service, box, {"DIGEST_AT": "06:00"})
    assert [j.name for j in jobs] == ["mail-sync", "mail-digest", "trade", "data-scan"]
    ledger = Ledger()
    ran = Scheduler(jobs, ledger, box).tick(datetime.now().replace(hour=8, minute=0))
    assert ran == ["mail-sync", "mail-digest", "trade", "data-scan"]
    titles = [a.title for a in box.sent]
    assert any(
        t.startswith("Mail digest") for t in titles
    )  # urgent-mail alerts are covered in test_notify
    assert not ledger.entries(kind="job.error")
