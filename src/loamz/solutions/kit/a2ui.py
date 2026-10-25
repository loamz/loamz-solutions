"""What a tool shows the person, as A2UI messages (https://a2ui.org, v0.9): the same standard a model is taught to write.

    ui = card(metrics(metric("Equity", "$151,288")), button("Approve", send("Approve plan 3f2a")), title="Plan 3f2a")
    messages = surface("plan-3f2a", ui)   # createSurface + updateComponents: a list of plain dicts

A tool returns the messages beside its text: the text is what the model reads (and what a client without a screen shows); the
messages are what the console draws. The components and their props are the console's catalog (apps/agents-console/src/lib/
a2ui/); the console drops a message that does not fit, so a mistake costs the card, never the chat. A button talks to the agent
only through `send`: its text is put in the chat as if the person had typed it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

CATALOG_ID = "https://loamz.dev/a2ui/chat/v1/catalog.json"
VERSION = "v0.9"
TONES = {
    "good": "success",
    "bad": "danger",
    "warn": "warning",
    "info": "info",
    "success": "success",
    "danger": "danger",
    "warning": "warning",
}


@dataclass
class Node:
    component: str
    props: dict[str, Any] = field(default_factory=dict)
    children: list[Node] = field(default_factory=list)
    slots: dict[str, Node] = field(
        default_factory=dict
    )  # a single child named by a prop: Button.child, Card.child


def _tone(tone: str | None) -> str | None:
    return TONES.get(tone) if tone else None


def _clean(props: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in props.items() if v is not None}


# ── the standard components ──────────────────────────────────────────────────
def text(value: str, variant: str = "body") -> Node:
    return Node("Text", {"text": value, "variant": variant})


def heading(value: str, level: str = "h4") -> Node:
    return Node("Text", {"text": value, "variant": level})


def caption(value: str) -> Node:
    return text(value, "caption")


def column(*children: Node, align: str | None = None) -> Node:
    return Node("Column", _clean({"align": align}), [c for c in children if c is not None])


def row(*children: Node, justify: str | None = None, align: str | None = "center") -> Node:
    return Node(
        "Row", _clean({"justify": justify, "align": align}), [c for c in children if c is not None]
    )


def divider() -> Node:
    return Node("Divider")


def image(
    url: str, variant: str = "mediumFeature", description: str = "", fit: str = "cover"
) -> Node:
    return Node("Image", {"url": url, "variant": variant, "description": description, "fit": fit})


def send(message: str) -> dict[str, Any]:
    """The action of a button that puts `message` in the chat."""
    return {"event": {"name": "send", "context": {"text": message}}}


def button(label: str, action: dict[str, Any], variant: str = "default") -> Node:
    return Node("Button", {"variant": variant, "action": action}, slots={"child": text(label)})


def card(*children: Node, title: str | None = None, subtitle: str | None = None) -> Node:
    header = [heading(title, "h3")] if title else []
    if subtitle:
        header.append(caption(subtitle))
    return Node("Card", slots={"child": column(*header, *children)})


# ── added by the chat catalog ────────────────────────────────────────────────
def metric(label: str, value: str, detail: str | None = None, trend: str | None = None) -> Node:
    return Node(
        "Metric", _clean({"label": label, "value": value, "detail": detail, "trend": trend})
    )


def metrics(*items: Node) -> Node:
    return row(*items, align="stretch")


def badge(value: str, tone: str | None = None) -> Node:
    return Node("Badge", _clean({"text": value, "tone": _tone(tone)}))


def progress(
    value: float, label: str | None = None, tone: str | None = None, maximum: float | None = None
) -> Node:
    return Node(
        "Progress", _clean({"value": value, "label": label, "tone": _tone(tone), "max": maximum})
    )


def callout(message: str, title: str | None = None, tone: str = "info") -> Node:
    return Node("Callout", _clean({"title": title, "text": message, "tone": _tone(tone)}))


def avatar(name: str, size: str = "md") -> Node:
    return Node("Avatar", {"name": name, "size": size})


# ── messages ─────────────────────────────────────────────────────────────────
def surface(
    surface_id: str, root: Node, data: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """The A2UI messages that draw `root` as a surface: createSurface, updateComponents (a flat list), and the data if any."""
    components: list[dict[str, Any]] = []
    counts: dict[str, int] = {}

    def add(node: Node, ident: str | None = None) -> str:
        counts[node.component] = counts.get(node.component, 0) + 1
        key = ident or f"{node.component.lower()}-{counts[node.component]}"
        entry: dict[str, Any] = {"id": key, "component": node.component, **node.props}
        components.append(entry)  # the parent first; the children take their ids after
        for prop, child in node.slots.items():
            entry[prop] = add(child)
        if node.children:
            entry["children"] = [add(c) for c in node.children]
        return key

    add(root, "root")
    messages: list[dict[str, Any]] = [
        {"version": VERSION, "createSurface": {"surfaceId": surface_id, "catalogId": CATALOG_ID}},
        {
            "version": VERSION,
            "updateComponents": {"surfaceId": surface_id, "components": components},
        },
    ]
    if data is not None:
        messages.append(
            {
                "version": VERSION,
                "updateDataModel": {"surfaceId": surface_id, "path": "/", "value": data},
            }
        )
    return messages


@dataclass
class Reply:
    """A tool's answer: `text` for the model, `a2ui` (optional) for the person's screen."""

    text: str
    a2ui: list[dict[str, Any]] | None = None

    @classmethod
    def shown(cls, text: str, a2ui: list[dict[str, Any]]) -> Reply:
        """A reply the person also sees as a card: the model is told not to read it back to them."""
        return cls(
            text
            + "\n\n(The person sees this as a card. Comment on what matters; do not list it again.)",
            a2ui,
        )


def money(x: float, signed: bool = False) -> str:
    sign = "+" if signed and x > 0 else "-" if x < 0 else ""
    return f"{sign}${abs(x):,.0f}"


def percent(x: float, signed: bool = False) -> str:
    return f"{x:+.1%}" if signed else f"{x:.1%}"
