import importlib.util
from pathlib import Path

from loamz.solutions.kit import Ledger


def load():
    spec = importlib.util.spec_from_file_location(
        "invoice_approver", Path(__file__).parent.parent / "examples" / "invoice_approver.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Box:
    def __init__(self):
        self.sent = []

    def send(self, alert):
        self.sent.append(alert)


def test_the_example_pays_small_invoices_asks_about_big_ones_and_refuses_the_rest(tmp_path):
    invoices = tmp_path / "i.csv"
    invoices.write_text(
        "id,supplier,amount\nA1,Acme Cloud,250\nA2,Acme Cloud,4000\nA3,Evil Corp,50\nA4,Initech,50000\nA5,Northwind LLC,900\n"
    )
    ledger, box, asked = Ledger(), Box(), []
    module = load()
    result = module.run(
        invoices,
        ledger,
        box,
        approver=lambda a, why: asked.append(a.params["invoice"]) or a.params["invoice"] == "A2",
        kill_file=str(tmp_path / "KILL"),
    )
    assert result == {
        "done": 3,
        "denied": 2,
        "refused": 0,
        "failed": 0,
    }  # A1, A2 (approved), A5 paid; A3 and A4 denied
    assert asked == ["A2"]
    assert {a.key for a in box.sent} == {"invoice:A3", "invoice:A4"}
    assert ledger.verify()["ok"] and len(ledger.entries(kind="action.done")) == 3


def test_a_declined_approval_is_recorded_and_the_kill_switch_stops_everything(tmp_path):
    invoices = tmp_path / "i.csv"
    invoices.write_text("id,supplier,amount\nB1,Acme Cloud,4000\nB2,Acme Cloud,10\n")
    module, ledger = load(), Ledger()
    assert module.run(invoices, ledger, Box(), kill_file=str(tmp_path / "KILL"))["refused"] == 1
    (tmp_path / "KILL").write_text("")
    assert module.run(invoices, Ledger(), Box(), kill_file=str(tmp_path / "KILL")) == {
        "done": 0,
        "denied": 2,
        "refused": 0,
        "failed": 0,
    }
