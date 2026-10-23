"""The `trade` commands: the same engine from a terminal, a cron job or a service."""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from loamz.solutions.kit.guard import Action
from loamz.solutions.kit.ledger import Ledger
from loamz.solutions.kit.llm import LLM, LLMError
from loamz.solutions.trading.backtest import backtest, load_csv, synth_bars
from loamz.solutions.trading.broker import PAPER_URL, AlpacaBroker, Broker, SimBroker
from loamz.solutions.trading.engine import Trader, TraderConfig
from loamz.solutions.trading.explain import explain
from loamz.solutions.trading.pnl import summarize
from loamz.solutions.trading.risk import RiskConfig
from loamz.solutions.trading.signals import HttpSignalModel, MovingAverageCross, SignalModel

LIVE_PHRASE = "I_UNDERSTAND_THE_RISK"


def env_float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def state_dir() -> Path:
    return Path(os.environ.get("STATE_DIR", ".state")) / "trader"


def risk_config(symbols: set[str]) -> RiskConfig:
    return RiskConfig(
        symbols=symbols,
        max_order_notional=env_float("TRADE_MAX_ORDER_NOTIONAL", 5000),
        max_position_pct=env_float("TRADE_MAX_POSITION_PCT", 0.20),
        max_daily_loss_pct=env_float("TRADE_MAX_DAILY_LOSS_PCT", 0.02),
        max_orders_per_day=int(env_float("TRADE_MAX_ORDERS_PER_DAY", 10)),
        cooldown_minutes=int(env_float("TRADE_COOLDOWN_MIN", 30)),
        kill_file=str(state_dir().parent / "KILL"),
    )


def trader_config(symbols: set[str]) -> TraderConfig:
    return TraderConfig(
        risk_config(symbols),
        min_confidence=env_float("TRADE_MIN_CONFIDENCE", 0.6),
        risk_per_trade=env_float("TRADE_RISK_PER_TRADE", 0.01),
    )


def symbols_from(args: argparse.Namespace) -> set[str]:
    raw = getattr(args, "symbols", None) or os.environ.get("TRADE_SYMBOLS", "")
    found = {s.strip().upper() for s in raw.split(",") if s.strip()}
    if not found:
        sys.exit("Name the symbols: --symbols AAPL,MSFT or TRADE_SYMBOLS in .env")
    return found


def model() -> SignalModel:
    url = os.environ.get("SIGNAL_URL")
    if url:
        return HttpSignalModel(url, os.environ.get("SIGNAL_TOKEN", ""))
    return MovingAverageCross(int(env_float("MA_FAST", 10)), int(env_float("MA_SLOW", 30)))


def broker_from_env() -> Broker:
    key, secret = os.environ.get("ALPACA_KEY_ID"), os.environ.get("ALPACA_SECRET_KEY")
    if not key or not secret:
        sys.exit(
            "Set ALPACA_KEY_ID and ALPACA_SECRET_KEY (an Alpaca paper account), or use `trade demo` for a simulation"
        )
    base = os.environ.get("ALPACA_BASE_URL", PAPER_URL)
    live = base.rstrip("/") != PAPER_URL
    if live and os.environ.get("TRADE_ALLOW_LIVE") != LIVE_PHRASE:
        sys.exit(
            f"A live account needs ALPACA_BASE_URL and TRADE_ALLOW_LIVE={LIVE_PHRASE}. Paper trading first."
        )
    return AlpacaBroker(key, secret, base_url=base, allow_live=live)


def ask_a_person(action: Action, reason: str) -> bool:
    if not sys.stdin.isatty():
        return False
    p = action.params
    answer = input(
        f"\nAPPROVE {p['side'].upper()} {p['qty']:g} {action.subject} at about {p['price']} ({reason})? [y/N] "
    )
    return answer.strip().lower() in ("y", "yes")


def llm() -> LLM | None:
    try:
        return LLM.from_env()
    except LLMError:
        return None


def build(args: argparse.Namespace) -> tuple[Trader, Ledger]:
    ledger = Ledger(state_dir() / "ledger.sqlite")
    return Trader(
        broker_from_env(), model(), trader_config(symbols_from(args)), ledger, approver=ask_a_person
    ), ledger


def run(args: argparse.Namespace) -> None:
    cmd = args.command
    if cmd == "demo":
        names = sorted(symbols_from(argparse.Namespace(symbols=args.symbols or "AAA,BBB")))
        bars = {s: synth_bars(args.days, seed=i + 1) for i, s in enumerate(names)}
        result = backtest(model(), bars, trader_config(set(names)))
        print(
            f"Synthetic prices, {args.days} days, {', '.join(names)}: a simulation, not a forecast\n"
        )
        print(result.summary.render())
        print(f"buy and hold  {result.benchmark_pct:+.2%} (equal weight, same window)")
        return
    if cmd == "backtest":
        names = sorted(symbols_from(args))
        if args.csv:
            bars = {names[0]: load_csv(args.csv)}
            names = names[:1]
        else:
            broker = broker_from_env()
            bars = {s: broker.bars(s, 500) for s in names}
        result = backtest(model(), bars, trader_config(set(names)))
        print(result.summary.render())
        print(f"buy and hold  {result.benchmark_pct:+.2%} (equal weight, same window)")
        return
    if cmd == "kill":
        path = Path(risk_config(set()).kill_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("stopped by a person\n")
        print(f"Kill switch ON ({path}). Nothing will trade until you run `trade resume`.")
        return
    if cmd == "resume":
        Path(risk_config(set()).kill_file).unlink(missing_ok=True)
        print("Kill switch off.")
        return
    ledger = Ledger(state_dir() / "ledger.sqlite")
    if cmd == "report":
        print(summarize(ledger).render())
        return
    if cmd == "explain":
        print(explain(ledger, args.symbol.upper(), llm()))
        return
    if cmd == "verify":
        print(ledger.verify())
        return
    trader, ledger = build(args)
    if cmd == "status":
        account, positions = trader.broker.account(), trader.broker.positions()
        print(
            f"mode {trader.broker.mode}   equity {account.equity:,.2f}   cash {account.cash:,.2f}   today {account.day_pnl:+,.2f}"
        )
        print(
            f"kill switch {'ON' if Path(trader.config.risk.kill_file).exists() else 'off'}   ledger {ledger.verify()}"
        )
        for p in positions:
            print(
                f"  {p.symbol:6} {p.qty:g} @ {p.avg_price:,.2f}  now {p.market_price:,.2f}  ({p.market_value:,.2f})"
            )
        return
    if cmd == "run-once":
        print(trader.run_once().render())
        return
    if cmd == "watch":
        while True:
            print(time.strftime("%H:%M"), trader.run_once().render(), sep="\n")
            time.sleep(args.every)


def add_parser(solutions) -> None:
    parser = solutions.add_parser("trade", help="the Paper Trader")
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="simulate on synthetic prices: no account, no network")
    demo.add_argument("--symbols", default="")
    demo.add_argument("--days", type=int, default=400)
    bt = commands.add_parser("backtest", help="replay history through the same engine and limits")
    bt.add_argument("--symbols", default="")
    bt.add_argument("--csv", default="", help="bars from a CSV file instead of the broker")
    for name, text in (
        ("status", "account, positions, kill switch"),
        ("run-once", "one pass over every symbol"),
        ("watch", "run on a loop"),
    ):
        sub = commands.add_parser(name, help=text)
        sub.add_argument("--symbols", default="")
        if name == "watch":
            sub.add_argument("--every", type=int, default=300)
    commands.add_parser("report", help="profit and loss from the ledger")
    ex = commands.add_parser(
        "explain", help="why did it do that? (plain language, from the ledger)"
    )
    ex.add_argument("symbol")
    commands.add_parser("verify", help="check the ledger has not been altered")
    commands.add_parser("kill", help="stop all trading at once")
    commands.add_parser("resume", help="turn the kill switch off")


__all__ = ["add_parser", "run", "SimBroker"]
