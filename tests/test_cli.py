"""The command line, end to end on synthetic data: no accounts, no network."""

import pytest

from loamz.solutions.cli import main


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    monkeypatch.chdir(tmp_path)
    for name in (
        "LLM_API_KEY",
        "OPENAI_API_KEY",
        "SIGNAL_URL",
        "ALPACA_KEY_ID",
        "ALPACA_SECRET_KEY",
        "LOAMZ_SOLUTIONS_KILL",
    ):
        monkeypatch.delenv(name, raising=False)


def test_the_mail_demo_prints_a_digest_and_can_be_asked(capsys):
    main(["mail", "demo"])
    out = capsys.readouterr().out
    assert "Needs you today" in out and "Signed contract attached" in out
    main(["mail", "--demo", "ask", "what is the contract about?"])
    assert "No model is configured" in capsys.readouterr().out


def test_the_trading_demo_simulates_and_reports(capsys):
    main(["trade", "demo", "--symbols", "AAA,BBB", "--days", "300"])
    out = capsys.readouterr().out
    assert "a simulation, not a forecast" in out and "win rate" in out and "buy and hold" in out


def test_the_kill_switch_command_stops_and_resumes(capsys, tmp_path):
    main(["trade", "kill"])
    assert (tmp_path / "state" / "KILL").exists() and "Kill switch ON" in capsys.readouterr().out
    main(["trade", "resume"])
    assert not (tmp_path / "state" / "KILL").exists()


def test_trading_without_an_account_says_what_to_set(capsys):
    with pytest.raises(SystemExit) as stopped:
        main(["trade", "status", "--symbols", "AAPL"])
    assert "ALPACA_KEY_ID" in str(stopped.value)


def test_a_live_account_is_refused_without_the_explicit_phrase(monkeypatch):
    monkeypatch.setenv("ALPACA_KEY_ID", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    monkeypatch.setenv("ALPACA_BASE_URL", "https://api.alpaca.markets")
    with pytest.raises(SystemExit) as stopped:
        main(["trade", "status", "--symbols", "AAPL"])
    assert "TRADE_ALLOW_LIVE" in str(stopped.value)


def test_explain_and_report_work_on_an_empty_ledger(capsys):
    main(["trade", "explain", "AAPL"])
    assert "Nothing has been recorded for AAPL" in capsys.readouterr().out
    main(["trade", "report"])
    assert "equity" in capsys.readouterr().out
