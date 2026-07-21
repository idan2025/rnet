"""SQLite inverted index for the local search database."""
from __future__ import annotations

import time
from typing import Dict, List, Optional

from rnet.db.connection import Database
from rnet.search.tokenizer import term_freqs


class Indexer:
    """Builds and queries a local inverted index over fetched documents."""

    def __init__(self, db: Database):
        self.db = db

    def index(self, url: str, host: str, title: str, text: str,
              content_hash: bytes = b"") -> int:
        """Upsert a document + its term frequencies. Returns the doc id."""
        now = int(time.time())
        existing = self.db.query_one(
            "SELECT doc_id FROM search_documents WHERE url=?", (url,)
        )
        if existing:
            doc_id = existing["doc_id"]
            self.db.execute("DELETE FROM search_terms WHERE doc_id=?", (doc_id,))
            self.db.execute(
                """UPDATE search_documents
                   SET host=?, title=?, fetched=?, content_hash=? WHERE doc_id=?""",
                (host, title, now, content_hash, doc_id),
            )
        else:
            cur = self.db.execute(
                """INSERT INTO search_documents (url, host, title, fetched, content_hash)
                   VALUES (?,?,?,?,?)""",
                (url, host, title, now, content_hash),
            )
            doc_id = int(cur.lastrowid)

        freqs = term_freqs(text)
        if freqs:
            self.db.executemany(
                "INSERT OR REPLACE INTO search_terms (term, doc_id, freq) VALUES (?,?,?)",
                [(t, doc_id, f) for t, f in freqs.items()],
            )
        return doc_id

    def remove(self, url: str) -> None:
        row = self.db.query_one(
            "SELECT doc_id FROM search_documents WHERE url=?", (url,)
        )
        if not row:
            return
        self.db.execute("DELETE FROM search_terms WHERE doc_id=?", (row["doc_id"],))
        self.db.execute("DELETE FROM search_documents WHERE doc_id=?", (row["doc_id"],))

    def query(self, terms: List[str], limit: int = 20) -> List[dict]:
        """Return ranked documents matching ``terms`` (any-term OR, more terms better)."""
        if not terms:
            return []
        # Score per doc = sum of freq across matched query terms.
        placeholders = ",".join("?" for _ in terms)
        rows = self.db.query(
            f"""SELECT st.doc_id, SUM(st.freq) AS score, COUNT(DISTINCT st.term) AS matched
                FROM search_terms st
                WHERE st.term IN ({placeholders})
                GROUP BY st.doc_id
                ORDER BY matched DESC, score DESC
                LIMIT ?""",
            (*terms, limit),
        )
        results = []
        for r in rows:
            doc = self.db.query_one(
                "SELECT url, host, title FROM search_documents WHERE doc_id=?",
                (r["doc_id"],),
            )
            if doc:
                results.append({
                    "url": doc["url"],
                    "host": doc["host"],
                    "title": doc["title"] or "",
                    "score": int(r["score"]),
                    "matched": int(r["matched"]),
                })
        return results

    def stats(self) -> dict:
        d = self.db.query_one("SELECT COUNT(*) AS n FROM search_documents")
        t = self.db.query_one("SELECT COUNT(*) AS n FROM search_terms")
        return {"documents": int(d["n"]), "terms": int(t["n"])}

    def list_documents(self) -> List[dict]:
        rows = self.db.query(
            "SELECT url, host, title, fetched FROM search_documents ORDER BY fetched DESC"
        )
        return [dict(r) for r in rows]