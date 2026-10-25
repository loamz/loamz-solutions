"""Run the solutions on their own: sync mail, trade during market hours, rescan data, send the morning digest.

    loamz-solutions run                 # everything that is configured, until stopped

Each job has its own interval and fails alone: an exception is written to the ledger and reported (an alert, once an hour at
most) and the other jobs carry on. The scheduler itself is a plain object driven by `tick(now)`, so it is tested without
sleeping. Stop it with Ctrl-C or SIGTERM; the kill switch (`trade kill`) stops trading without stopping the rest.
"""

from __future__ import annotations

import logging
import signal
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from loamz.solutions.kit.ledger import Ledger
from loamz.solutions.kit.notify import Alert, Notifier

logger = logging.getLogger(__name__)


@dataclass
class Job:
    name: str
    run: Callable[[], object]
    every: timedelta | None = None  # an interval...
    daily_at: tuple[int, int] | None = None  # ...or a time of day (hour, minute)
    tz: ZoneInfo | None = (
        None  # the daily_at clock: this zone (a market's), or the machine's when None
    )
    last: datetime | None = field(default=None, repr=False)

    def due(self, now: datetime) -> bool:
        if self.tz is not None:
            now = now.astimezone(self.tz)
            if self.last is not None:
                self.last = self.last.astimezone(self.tz)
        if self.daily_at is not None:
            target = now.replace(
                hour=self.daily_at[0], minute=self.daily_at[1], second=0, microsecond=0
            )
            return now >= target and (self.last is None or self.last < target)
        return self.last is None or now - self.last >= (self.every or timedelta(minutes=10))


class Scheduler:
    def __init__(self, jobs: list[Job], ledger: Ledger, notifier: Notifier) -> None:
        self.jobs, self.ledger, self.notifier = jobs, ledger, notifier
        self.stopping = False

    def tick(self, now: datetime) -> list[str]:
        """Run every job that is due; returns the names that ran."""
        ran: list[str] = []
        for job in self.jobs:
            if not job.due(now):
                continue
            job.last = now
            try:
                job.run()
                ran.append(job.name)
            except Exception as error:  # noqa: BLE001 - one failing job must not stop the others
                logger.exception("job %s failed", job.name)
                self.ledger.append("job.error", job.name, error=f"{type(error).__name__}: {error}")
                self.notifier.send(
                    Alert(
                        f"job:{job.name}:{now:%Y-%m-%d-%H}",
                        f"{job.name} failed",
                        f"{type(error).__name__}: {error}",
                        "warn",
                    )
                )
        return ran

    def run_forever(self, poll: float = 5.0) -> None:
        def stop(*_: object) -> None:
            self.stopping = True

        signal.signal(signal.SIGINT, stop)
        signal.signal(signal.SIGTERM, stop)
        while not self.stopping:
            self.tick(datetime.now())
            time.sleep(poll)


def clock_time(text: str) -> tuple[int, int]:
    hour, _, minute = text.partition(":")
    return int(hour), int(minute or 0)


def jobs_for(service, notifier: Notifier, env: dict[str, str]) -> list[Job]:
    """The jobs for whatever `service` has configured. Intervals and the digest time come from `env` (seconds, HH:MM)."""

    def seconds(name: str, default: int) -> timedelta:
        return timedelta(seconds=int(env.get(name, default)))

    jobs: list[Job] = []
    if service.mail is not None:
        service.mail.notifier = notifier
        mail = service.mail

        def digest() -> None:
            today = datetime.now()
            notifier.send(
                Alert(
                    f"digest:{today:%Y-%m-%d}",
                    f"Mail digest, {today:%A %d %B}",
                    mail.digest(hours=24),
                    "info",
                )
            )

        jobs.append(Job("mail-sync", lambda: mail.sync(days=2), every=seconds("MAIL_EVERY", 600)))
        jobs.append(Job("mail-digest", digest, daily_at=clock_time(env.get("DIGEST_AT", "07:30"))))
    from loamz.solutions.markets import cli as markets

    if markets.configured(env):
        def refresh_markets() -> None:
            from loamz.solutions.markets.alpaca_data import last_settled_day

            data, warehouse = markets.build(env)
            warehouse.update(data, today=last_settled_day(datetime.now().astimezone()))

        # After the US close has settled: each evening adds the day just finished.
        jobs.append(
            Job(
                "markets-update",
                refresh_markets,
                daily_at=clock_time(env.get("MARKETS_UPDATE_AT", "18:00")),
                tz=ZoneInfo("America/New_York"),
            )
        )
    if getattr(service, "desk", None) is not None:
        from loamz.solutions.trading.desk import rebalance_day

        desk, new_york = service.desk, ZoneInfo("America/New_York")
        desk.notifier = notifier

        def evening() -> None:
            today = datetime.now(new_york).date()
            if today.weekday() >= 5:
                return  # no new close on a weekend
            with service.lock:
                desk.evening(rebalance=rebalance_day(today))

        def follow_up() -> None:
            with service.lock:
                desk.follow_up()

        # After the day's prices are stored: risk exits go out, and on Fridays the week's plan waits for approval.
        jobs.append(
            Job(
                "desk-evening",
                evening,
                daily_at=clock_time(env.get("DESK_EVENING_AT", "18:15")),
                tz=new_york,
            )
        )
        # After the open: fills are recorded, and buys that waited for the sells' cash are sent.
        for at in ("09:45", "11:00"):
            jobs.append(
                Job(f"desk-follow-up-{at}", follow_up, daily_at=clock_time(at), tz=new_york)
            )
    if service.trader is not None and getattr(service, "desk", None) is None:
        trader = service.trader
        trader.notifier = notifier

        def trade() -> None:
            if trader.broker.is_open():  # outside market hours there is nothing to decide
                trader.run_once()

        jobs.append(Job("trade", trade, every=seconds("TRADE_EVERY", 300)))
        if env.get("WATCH_RULES"):
            from loamz.solutions.trading.watch import Watcher, parse_rules

            watcher = Watcher(
                trader.broker,
                parse_rules(env["WATCH_RULES"]),
                notifier,
                trader.ledger,
                model=trader.model,
                trader=trader,
            )
            jobs.append(
                Job(
                    "watch",
                    lambda: watcher.run_once() if trader.broker.is_open() else None,
                    every=seconds("WATCH_EVERY", 300),
                )
            )
    if service.data is not None:
        data = service.data
        jobs.append(Job("data-scan", data.scan, every=seconds("DATA_EVERY", 3600)))
    return jobs
