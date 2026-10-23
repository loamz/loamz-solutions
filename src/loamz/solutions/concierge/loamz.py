"""Loamz as a source: what the deployment's catalog, document collections and memory hold, and a way to search them."""

from __future__ import annotations

from typing import Any

from loamz.core.exceptions import HttpResponseError
from loamz.data import DataClient


class LoamzSource:
    def __init__(self, client: DataClient, user_id: str = "") -> None:
        self.client, self.user_id = client, user_id

    def inventory(self, table_limit: int = 200) -> dict[str, Any]:
        """Catalogs, schemas, tables and document collections the caller may see. A part that fails is reported, not fatal."""
        out: dict[str, Any] = {"catalogs": [], "tables": [], "collections": [], "errors": []}
        try:
            for catalog in self.client.catalog.catalogs():
                name = catalog["name"]
                out["catalogs"].append(name)
                for schema in self.client.catalog.schemas(name):
                    for table in self.client.catalog.tables(name, schema["name"]):
                        if len(out["tables"]) < table_limit:
                            out["tables"].append(f"{name}.{schema['name']}.{table['name']}")
        except HttpResponseError as error:
            out["errors"].append(f"catalog: {error.detail or error}")
        try:
            out["collections"] = [
                f"{c['name']} ({c.get('documents', '?')} documents)"
                for c in self.client.documents.collections()
            ]
        except HttpResponseError as error:
            out["errors"].append(f"documents: {error.detail or error}")
        return out

    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        hits: list[dict[str, Any]] = []
        try:
            for collection in self.client.documents.collections():
                answer = self.client.documents.search(collection["name"], query, limit=limit)
                for h in answer.get("hits", [])[:limit]:
                    hits.append(
                        {
                            "where": "loamz:documents",
                            "title": h.get("document") or h.get("title") or collection["name"],
                            "ref": f"{collection['name']}/{h.get('document', '')}",
                            "snippet": str(h.get("text") or h.get("snippet") or "")[:300],
                        }
                    )
        except HttpResponseError:
            pass
        if self.user_id:
            try:
                for m in self.client.memory.query(user_id=self.user_id, text_query=query)[:limit]:
                    text = "".join(b.get("text", "") for b in m["record"].get("content", []))
                    hits.append(
                        {
                            "where": "loamz:memory",
                            "title": text[:60],
                            "ref": m["record"].get("id", ""),
                            "snippet": text[:300],
                        }
                    )
            except HttpResponseError:
                pass
        return hits[:limit]
