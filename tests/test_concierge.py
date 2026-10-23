"""The Data Concierge: reading files, indexing, searching across sources, the map, answers and suggestions."""

import os
import time
from pathlib import Path

import httpx

from loamz.solutions.concierge import DataConcierge, DataIndex, FolderConnector
from loamz.solutions.concierge.demo import make_demo_folder
from loamz.solutions.concierge.files import read_text
from loamz.solutions.kit import Ledger
from loamz.solutions.kit.llm import LLM
from loamz.solutions.mail import MailConcierge, MailStore, Triager
from loamz.solutions.mail.demo import PROFILE, DemoConnector
from loamz.solutions.mail.triage import RuleClassifier


def folder(tmp_path) -> Path:
    return make_demo_folder(tmp_path / "files")


def concierge(tmp_path, **options) -> DataConcierge:
    root = folder(tmp_path)
    c = DataConcierge(DataIndex(tmp_path / "i.sqlite"), FolderConnector([root]), **options)
    c.scan()
    return c


def test_text_like_files_are_read_and_others_are_not(tmp_path):
    root = folder(tmp_path)
    assert "indemnity cap" in read_text(root / "notes/northwind-call.md")
    assert "NAV up 2.3%" in read_text(
        root / "reports/fund-dashboard.html"
    ) and "<h1>" not in read_text(root / "reports/fund-dashboard.html")
    assert "Table with columns: symbol, qty, avg_price. 3 rows" in read_text(
        root / "finance/positions.csv"
    )
    assert '"timezone"' in read_text(root / "config/settings.json")
    assert read_text(root / "contracts/northwind-signed.pdf") is None


def test_a_scan_indexes_readable_files_and_counts_what_it_cannot_read(tmp_path):
    root = folder(tmp_path)
    (root / ".hidden").mkdir()
    (root / ".hidden" / "secret.txt").write_text("do not index")
    c = DataConcierge(DataIndex(), FolderConnector([root]))
    report = c.scan()
    assert report.files_seen == 9 and report.files_new == 9
    assert report.unreadable == {".pdf": 1, ".png": 1}
    assert c.index.search("secret") == []
    again = c.scan()
    assert again.files_new == 0  # unchanged files are not re-indexed
    path = root / "notes/ideas.md"
    path.write_text("# Ideas\n- a brand new idea about zeppelins\n")
    os.utime(path, (time.time() + 60, time.time() + 60))
    assert c.scan().files_new == 1 and c.index.search("zeppelins")


def test_search_finds_files_with_a_snippet_and_a_path(tmp_path):
    c = concierge(tmp_path)
    hits = c.search("indemnity cap")
    assert {h["title"] for h in hits} == {"northwind-call.md", "northwind-summary.txt"}
    assert all("[indemnity]" in h["snippet"] or "[cap]" in h["snippet"] for h in hits)
    assert hits[0]["ref"].endswith(".md") or hits[0]["ref"].endswith(".txt")
    assert c.search("zzzz-nothing") == [] and c.search("a b") == []


def test_the_map_says_where_data_lives_what_is_unreadable_and_what_you_can_do(tmp_path):
    text = concierge(tmp_path).inventory()
    assert "## Files" in text and "9 readable files indexed" in text
    assert "md" in text and "csv" in text and "Not readable yet" in text and ".pdf" in text
    assert "You can: search by words" in text


def test_mail_is_searched_alongside_files_and_appears_on_the_map(tmp_path):
    mail = MailConcierge(DemoConnector(), MailStore(), Triager(RuleClassifier(PROFILE)), Ledger())
    mail.sync()
    c = concierge(tmp_path, mail=mail)
    where = {h["where"].split(" ")[0] for h in c.search("contract indemnity")}
    assert where == {"files", "mail"}
    assert "## Mail" in c.inventory() and "prioritised digest" in c.inventory()


def llm(answer: str) -> LLM:
    seen = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": answer}}]})

    model = LLM("k", transport=httpx.MockTransport(handle))
    model.seen = seen  # type: ignore[attr-defined]
    return model


def test_asking_cites_the_matches_and_degrades_without_a_model(tmp_path):
    plain = concierge(tmp_path)
    no_model = plain.ask("what is the indemnity cap?")
    assert "No model is configured" in no_model.text and no_model.sources
    model = llm("The cap is $2M [notes/northwind-call.md]")
    answered = concierge(tmp_path / "again", llm=model).ask("what is the indemnity cap?")
    assert "$2M" in answered.text and answered.sources
    prompt = model.seen[0].content.decode()
    assert "indemnity cap" in prompt and "using only the excerpts" in prompt
    assert "nothing about that" in plain.ask("zzzz-nothing-here").text


def test_suggestions_come_from_what_exists(tmp_path):
    c = concierge(tmp_path)
    ideas = c.suggest()
    assert "2 CSV files" in ideas and "extractor" in ideas
    assert "model says" in concierge(tmp_path / "x", llm=llm("model says: do this")).suggest()


def test_every_source_gets_a_share_of_the_results(tmp_path):
    mail = MailConcierge(DemoConnector(), MailStore(), Triager(RuleClassifier(PROFILE)), Ledger())
    mail.sync()
    c = concierge(tmp_path, mail=mail)
    hits = c.search("contract indemnity cap legal northwind countersign signed", limit=6)
    wheres = [h["where"].split(" ")[0] for h in hits]
    assert "mail" in wheres and "files" in wheres and len(hits) == 6
    assert wheres[0] != wheres[1]  # best of each source first, not all of one
