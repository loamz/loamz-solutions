"""What a tool shows the person, as a json-render spec (https://json-render.dev): the same standard parts a model is taught.

    ui = node("Card", node("Metric", label="Equity", value="$151,288"), title="Paper account")
    spec(ui)       # {"root": "card-1", "elements": {"card-1": {"type": "Card", "props": {...}, "children": [...]}, ...}}

A tool returns the spec beside its text: the text is what the model reads (and what a client without a screen shows); the
spec is what the console draws. The parts and their props are the console's catalog (apps/agents-console/src/lib/genui/
catalog.ts); a prop left out is read as null, and the console drops a spec that does not fit, so a mistake costs the card,
never the chat. A `Button` talks to the agent only by `send`: its text is put in the chat as if the person typed it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Node:
    type: str
    props: dict[str, Any]
    children: list[Node] = field(default_factory=list)
    on: dict[str, Any] | None = None


def node(type: str, /, *children: Node, on: dict[str, Any] | None = None, **props: Any) -> Node:
    return Node(type, props, [c for c in children if c is not None], on)


def send(text: str) -> dict[str, Any]:
    """The `on` of a Button that puts `text` in the chat."""
    return {"press": {"action": "send", "params": {"text": text}}}


def spec(root: Node) -> dict[str, Any]:
    """The flat json-render spec of a tree: `{"root": key, "elements": {key: {type, props, children, on?}}}`."""
    elements: dict[str, Any] = {}
    counts: dict[str, int] = {}

    def add(n: Node) -> str:
        counts[n.type] = counts.get(n.type, 0) + 1
        key = f"{n.type.lower()}-{counts[n.type]}"
        elements[key] = {}  # reserves the key before the children take theirs
        element: dict[str, Any] = {
            "type": n.type,
            "props": n.props,
            "children": [add(c) for c in n.children],
        }
        if n.on:
            element["on"] = n.on
        elements[key] = element
        return key

    return {"root": add(root), "elements": elements}


@dataclass
class Reply:
    """A tool's answer: `text` for the model, `spec` (optional) for the person's screen."""

    text: str
    spec: dict[str, Any] | None = None

    @classmethod
    def shown(cls, text: str, spec: dict[str, Any]) -> Reply:
        """A reply the person also sees as a card: the model is told not to read it back to them."""
        return cls(
            text
            + "\n\n(The person sees this as a card. Comment on what matters; do not list it again.)",
            spec,
        )


def money(x: float, signed: bool = False) -> str:
    sign = "+" if signed and x > 0 else "-" if x < 0 else ""
    return f"{sign}${abs(x):,.0f}"


def percent(x: float, signed: bool = False) -> str:
    return f"{x:+.1%}" if signed else f"{x:.1%}"
