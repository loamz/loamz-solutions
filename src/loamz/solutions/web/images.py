"""Pictures for a rich answer: a search that returns addresses an agent can put in a card.

    finder = ImageFinder()
    finder.find("Spain world cup winners celebrating", limit=6)
    # [Picture(title="...", address="https://tse1.mm.bing.net/th?id=...", page="https://news.example/...", width=474, height=266), ...]

The address is the search engine's own small copy of the picture (a thumbnail), not the original: it is fast, stable and small enough
for the console's picture proxy, and the original's site often refuses to be loaded from elsewhere. `page` is where the picture came
from, to credit. Needs no key (DuckDuckGo); the search function can be replaced.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Picture:
    title: str
    address: str
    page: str
    width: int | None = None
    height: int | None = None


def ddgs_images(query: str, limit: int) -> list[dict[str, Any]]:
    from ddgs import DDGS

    return list(DDGS().images(query, max_results=limit))


def https(url: object) -> str | None:
    text = str(url or "").strip()
    parts = urlsplit(text)
    return text if parts.scheme == "https" and parts.hostname and not parts.username else None


class ImageFinder:
    def __init__(self, search: Callable[[str, int], list[dict[str, Any]]] = ddgs_images) -> None:
        self._search = search

    def find(self, query: str, limit: int = 6) -> list[Picture]:
        limit = max(1, min(int(limit), 10))
        found: list[Picture] = []
        seen: set[str] = set()
        for hit in self._search(query, limit * 2):  # some hits have no usable address
            address = https(hit.get("thumbnail")) or https(hit.get("image"))
            if not address or address in seen:
                continue
            seen.add(address)
            found.append(
                Picture(
                    title=" ".join(str(hit.get("title") or "").split())[:140],
                    address=address,
                    page=https(hit.get("url")) or "",
                    width=hit.get("width") if isinstance(hit.get("width"), int) else None,
                    height=hit.get("height") if isinstance(hit.get("height"), int) else None,
                )
            )
            if len(found) == limit:
                break
        return found

    def describe(self, query: str, limit: int = 6) -> str:
        pictures = self.find(query, limit)
        if not pictures:
            return f"No pictures found for: {query}"
        lines = [
            f"Pictures for '{query}'. Use the picture address in an Image; credit the page when it matters.\n"
        ]
        for i, p in enumerate(pictures, 1):
            size = f" ({p.width}x{p.height})" if p.width and p.height else ""
            lines.append(
                f"{i}. {p.title or 'untitled'}{size}\n   picture: {p.address}\n   page: {p.page or 'unknown'}"
            )
        return "\n".join(lines)
