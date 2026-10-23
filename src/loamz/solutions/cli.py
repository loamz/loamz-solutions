"""`loamz-solutions`: run the solutions from a terminal or a scheduler.

    loamz-solutions mail demo                    # a synthetic inbox, no credentials needed
    loamz-solutions mail sync                    # read the mailbox, triage what is new
    loamz-solutions mail digest [--send]         # what needs you, in order
    loamz-solutions mail ask "what did Priya say about the contract?"
    loamz-solutions mail --demo ask "who is waiting on me?"      # ask the demo inbox
    loamz-solutions mail watch --every 600       # sync and refresh the digest on a loop

    loamz-solutions trade demo                   # simulate on synthetic prices, no account needed
    loamz-solutions trade backtest --symbols AAPL,MSFT
    loamz-solutions trade status | run-once | watch --every 300 | report | explain AAPL | verify
    loamz-solutions trade kill | resume          # the kill switch

Settings come from the environment (a `.env` file is read): MAIL_IMAP_HOST, MAIL_USER, MAIL_PASSWORD, MAIL_FOLDER,
MAIL_VIPS (comma separated addresses or @domains), MAIL_ABOUT (a sentence about you), STATE_DIR (default .state), TRADE_*\n(see .env.example), ALPACA_KEY_ID / ALPACA_SECRET_KEY, SIGNAL_URL / SIGNAL_TOKEN (your own model), and for the
model LLM_API_KEY / LLM_BASE_URL / LLM_MODEL. Without a model, rules alone rank your mail.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from loamz.solutions.kit.ledger import Ledger
from loamz.solutions.kit.llm import LLM, LLMError
from loamz.solutions.mail import (
    ImapConnector,
    LLMClassifier,
    MailConcierge,
    MailStore,
    Profile,
    RuleClassifier,
    Triager,
)


def load_env(path: str = ".env") -> None:
    file = Path(path)
    if not file.exists():
        return
    for raw in file.read_text().splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def need(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.exit(f"Set {name} (see .env.example)")
    return value


def model() -> LLM | None:
    try:
        return LLM.from_env()
    except LLMError:
        return None


def state_dir() -> Path:
    return Path(os.environ.get("STATE_DIR", ".state"))


def build_mail(demo: bool = False) -> MailConcierge:
    state = state_dir() / ("demo" if demo else "mail")
    state.mkdir(parents=True, exist_ok=True)
    if demo:
        from loamz.solutions.mail.demo import PROFILE, DemoConnector

        profile, connector = PROFILE, DemoConnector()
    else:
        user = need("MAIL_USER")
        profile = Profile(
            addresses={user.lower()},
            vips={v.strip() for v in os.environ.get("MAIL_VIPS", "").split(",") if v.strip()},
            about=os.environ.get("MAIL_ABOUT", ""),
            never_noise={
                v.strip() for v in os.environ.get("MAIL_KEEP", "").split(",") if v.strip()
            },
        )
        connector = ImapConnector(
            need("MAIL_IMAP_HOST"),
            user,
            need("MAIL_PASSWORD"),
            folder=os.environ.get("MAIL_FOLDER", "INBOX"),
        )
    llm = model()
    triager = Triager(RuleClassifier(profile), LLMClassifier(llm, profile) if llm else None)
    return MailConcierge(
        connector, MailStore(state / "mail.sqlite"), triager, Ledger(state / "ledger.sqlite")
    )


def mail(args: argparse.Namespace) -> None:
    concierge = build_mail(demo=args.command == "demo" or args.demo)
    if args.command in ("demo", "sync", "watch"):
        report = concierge.sync(days=getattr(args, "days", 2))
        print(
            f"fetched {report.fetched}, new {report.new}, triaged {report.triaged}: {report.by_priority}"
        )
    if args.command in ("demo", "digest", "watch"):
        text = concierge.digest(hours=getattr(args, "hours", 24))
        print(text)
        if getattr(args, "send", False):
            from loamz.solutions.mail.digest import SmtpSender

            SmtpSender(need("SMTP_HOST"), need("MAIL_USER"), need("MAIL_PASSWORD")).send(
                "Mail digest", text
            )
            print("digest sent")
    if args.command == "ask":
        answer = concierge.ask(args.question, model())
        print(answer.text)
        for hit in answer.sources[:5]:
            print(f"  [{hit['id']}] {hit['at'][:10]} {hit['author']}: {hit['title']}")
    if args.command == "watch":
        while True:
            time.sleep(args.every)
            report = concierge.sync(days=2)
            print(f"{time.strftime('%H:%M')} fetched {report.fetched}, new {report.new}")


def main(argv: list[str] | None = None) -> None:
    load_env()
    parser = argparse.ArgumentParser(
        prog="loamz-solutions",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    solutions = parser.add_subparsers(dest="solution", required=True)
    mail_parser = solutions.add_parser("mail", help="the Mail Concierge")
    mail_parser.add_argument(
        "--demo", action="store_true", help="use the synthetic inbox (state under .state/demo)"
    )
    commands = mail_parser.add_subparsers(dest="command", required=True)
    commands.add_parser("demo", help="run on a synthetic inbox")
    sync = commands.add_parser("sync", help="read the mailbox and triage new mail")
    sync.add_argument("--days", type=int, default=2)
    digest = commands.add_parser("digest", help="print the digest")
    digest.add_argument("--hours", type=int, default=24)
    digest.add_argument(
        "--send", action="store_true", help="also mail it to yourself (needs SMTP_HOST)"
    )
    ask = commands.add_parser("ask", help="ask a question about your mail")
    ask.add_argument("question")
    watch = commands.add_parser("watch", help="sync on a loop")
    watch.add_argument("--every", type=int, default=600)
    watch.add_argument("--days", type=int, default=2)
    watch.add_argument("--hours", type=int, default=24)
    from loamz.solutions.trading import cli as trading

    trading.add_parser(solutions)
    args = parser.parse_args(argv)
    if args.solution == "mail":
        mail(args)
    elif args.solution == "trade":
        trading.run(args)


if __name__ == "__main__":
    main()
