"""The A2UI messages the tools return: a flat list of components with a root, children by id, a button that only sends words."""

from __future__ import annotations

from loamz.solutions.kit.a2ui import (
    CATALOG_ID,
    badge,
    button,
    card,
    column,
    heading,
    metric,
    metrics,
    row,
    send,
    surface,
    text,
)

KNOWN = {
    "Text", "Image", "Icon", "Video", "AudioPlayer", "Row", "Column", "List", "Card", "Tabs", "Modal", "Divider", "Button",
    "TextField", "CheckBox", "ChoicePicker", "Slider", "DateTimeInput", "Metric", "Badge", "Progress", "Callout", "Avatar",
}  # fmt: skip


def components(messages):
    return next(m["updateComponents"]["components"] for m in messages if "updateComponents" in m)


def test_a_surface_is_a_create_message_and_a_flat_list_with_a_root():
    messages = surface(
        "plan",
        card(
            metrics(metric("Equity", "$1")),
            button("Approve", send("Approve plan x"), "primary"),
            title="Plan x",
        ),
    )
    assert messages[0] == {
        "version": "v0.9",
        "createSurface": {"surfaceId": "plan", "catalogId": CATALOG_ID},
    }
    parts = components(messages)
    by_id = {c["id"]: c for c in parts}
    assert len(by_id) == len(parts) and by_id["root"]["component"] == "Card"
    assert {c["component"] for c in parts} <= KNOWN


def test_every_child_an_id_names_exists_and_nothing_is_orphaned():
    parts = components(
        surface("s", card(row(badge("BUY", "good"), text("x")), column(heading("h")), title="t"))
    )
    by_id = {c["id"]: c for c in parts}
    named = set()
    for c in parts:
        named |= set(c.get("children", [])) | ({c["child"]} if "child" in c else set())
    assert named <= set(by_id) and set(by_id) - named == {"root"}


def test_a_button_names_its_label_and_only_sends_words():
    parts = components(surface("s", card(button("Approve", send("Approve plan x"), "primary"))))
    button_ = next(c for c in parts if c["component"] == "Button")
    assert button_["action"] == {"event": {"name": "send", "context": {"text": "Approve plan x"}}}
    label = next(c for c in parts if c["id"] == button_["child"])
    assert label["component"] == "Text" and label["text"] == "Approve"


def test_props_left_out_are_left_out_not_null_and_tones_are_the_catalogs():
    parts = components(surface("s", card(badge("SELL", "bad"), metric("Equity", "$1"))))
    assert next(c for c in parts if c["component"] == "Badge")["tone"] == "danger"
    assert "detail" not in next(c for c in parts if c["component"] == "Metric")


def test_data_goes_in_its_own_message():
    messages = surface("s", card(text("x")), data={"a": 1})
    assert messages[-1] == {
        "version": "v0.9",
        "updateDataModel": {"surfaceId": "s", "path": "/", "value": {"a": 1}},
    }
