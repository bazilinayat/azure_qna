import logging

from sqlalchemy import text

from app.db import fts
from app.db.connection import get_session
from app.search.search_result import SearchResult

log = logging.getLogger(__name__)

_SQL = text(f"""
    SELECT
        c.id,
        c.document_id,
        c.chunk_index,
        c.content,
        c.header_path,
        d.title,
        d.url,
        d.category,
        {fts.BM25_EXPRESSION} AS score

    FROM {fts.FTS_TABLE}

    JOIN chunks c
        ON c.id = {fts.FTS_TABLE}.rowid

    JOIN documents d
        ON d.id = c.document_id

    WHERE {fts.FTS_TABLE} MATCH :query

    ORDER BY score

    LIMIT :limit
""")


class KeywordSearch:
    """BM25 search over the FTS5 index."""

    def search(self, query: str, limit: int = 20) -> list[SearchResult]:

        # Raw user text is not a valid FTS5 expression; see fts.build_match_query.
        match_query = fts.build_match_query(query)

        if not match_query:
            return []

        # The session is closed on every path, including errors — the previous
        # version leaked one connection per search call.
        with get_session() as session:

            try:
                rows = session.execute(
                    _SQL,
                    {"query": match_query, "limit": limit},
                ).all()

            except Exception:
                log.exception("Keyword search failed for query %r", query)
                return []

        return [
            SearchResult(
                id=row.id,
                document_id=row.document_id,
                chunk_index=row.chunk_index,
                title=row.title,
                url=row.url,
                category=row.category,
                header_path=row.header_path,
                content=row.content,
                # bm25() returns negative values where more negative is a better
                # match, so it is negated to make larger mean better.
                score=-row.score,
                source="keyword",
            )
            for row in rows
        ]
