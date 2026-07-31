from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.config import DATABASE_PATH, DATABASE_URL

# --------------------------------------------------
# Engine
# --------------------------------------------------

DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    DATABASE_URL,
    echo=False,
    future=True,
)


@event.listens_for(engine, "connect")
def _configure_sqlite(dbapi_connection, connection_record):
    """Apply pragmas that matter for a write-heavy batch pipeline.

    WAL plus relaxed sync turns the ingestion insert loop from fsync-bound into
    CPU-bound. Foreign keys are off by default in SQLite, so the ON DELETE
    CASCADE from chunks to documents only works once they are enabled.
    """

    cursor = dbapi_connection.cursor()

    cursor.execute("PRAGMA journal_mode = WAL")
    cursor.execute("PRAGMA synchronous = NORMAL")
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.execute("PRAGMA temp_store = MEMORY")

    # Negative value means kibibytes rather than pages: 64 MB of page cache.
    cursor.execute("PRAGMA cache_size = -65536")

    cursor.close()


# --------------------------------------------------
# Session factory
# --------------------------------------------------

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    future=True,
)


def get_engine():
    """Return the shared SQLAlchemy engine."""

    return engine


def get_session():
    """Return a new SQLAlchemy session.

    Callers own the session and must close it; prefer using it as a context
    manager (`with get_session() as session:`).
    """

    return SessionLocal()


def test_connection() -> None:
    """Verify that the database can be opened."""

    with engine.connect():
        print(f"Connected to {DATABASE_PATH}")


if __name__ == "__main__":
    test_connection()
