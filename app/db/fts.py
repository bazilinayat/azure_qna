"""SQLite FTS5 full-text index over the chunks table.

The index is an *external content* table: it stores only the inverted index and
reads the actual text from `chunks` via rowid. That keeps it a fraction of the
size of a standalone FTS table, at the cost of needing an explicit rebuild after
ingestion (there are no sync triggers, because ingestion is a batch job).
"""

import logging
import re

from sqlalchemy import text
from sqlalchemy.engine import Connection

log = logging.getLogger(__name__)

FTS_TABLE = "chunks_fts"

# `porter` stemming lets "deploying" match "deploy"; `unicode61` handles
# punctuation and case folding. `remove_diacritics` keeps accented text usable.
CREATE_FTS_SQL = f"""
CREATE VIRTUAL TABLE IF NOT EXISTS {FTS_TABLE} USING fts5(
    content,
    header_path,
    content='chunks',
    content_rowid='id',
    tokenize="porter unicode61 remove_diacritics 2"
)
"""

# Header matches are weighted higher than body matches: a query hitting the
# section breadcrumb is a stronger signal than one buried in prose.
BM25_EXPRESSION = f"bm25({FTS_TABLE}, 1.0, 2.0)"

# FTS5 treats these as query syntax; anything outside them is a plain term.
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+")


def create(connection: Connection) -> None:
    """Create the FTS virtual table if it does not exist."""

    connection.execute(text(CREATE_FTS_SQL))


def drop(connection: Connection) -> None:
    """Drop the FTS virtual table."""

    connection.execute(text(f"DROP TABLE IF EXISTS {FTS_TABLE}"))


def rebuild(connection: Connection) -> int:
    """Rebuild the index from the current contents of `chunks`.

    Returns the number of indexed rows.
    """

    create(connection)

    connection.execute(
        text(f"INSERT INTO {FTS_TABLE}({FTS_TABLE}) VALUES('rebuild')")
    )

    # Merges the b-tree segments into one, which measurably speeds up queries.
    connection.execute(
        text(f"INSERT INTO {FTS_TABLE}({FTS_TABLE}) VALUES('optimize')")
    )

    return count(connection)


def count(connection: Connection) -> int:
    """Number of rows currently in the FTS index."""

    return connection.execute(
        text(f"SELECT count(*) FROM {FTS_TABLE}")
    ).scalar_one()


def exists(connection: Connection) -> bool:
    """Whether the FTS virtual table has been created."""

    found = connection.execute(
        text(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name = :name"
        ),
        {"name": FTS_TABLE},
    ).first()

    return found is not None


def build_match_query(query: str) -> str:
    """Turn arbitrary user text into a safe FTS5 MATCH expression.

    Raw user input cannot go into MATCH: a stray quote, hyphen, asterisk or the
    bare word "AND" is either a syntax error or silently changes the query. So
    every alphanumeric run is extracted and re-quoted as a literal phrase.

    Terms are combined with OR rather than the FTS5 default of AND. Recall
    matters more than precision here because these results are fused with the
    vector results and then reranked, and an AND query over a long natural
    question usually matches nothing at all.
    """

    terms = _TOKEN_PATTERN.findall(query)

    if not terms:
        return ""

    return " OR ".join(f'"{term}"' for term in terms)
