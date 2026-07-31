import logging
from argparse import ArgumentParser

from app.db import fts
from app.db.connection import get_engine
from app.db.schema import metadata

log = logging.getLogger(__name__)


def create_database(reset: bool = False) -> None:
    """Create all database tables, including the FTS5 index."""

    engine = get_engine()

    with engine.begin() as connection:

        if reset:
            log.info("Dropping existing tables")

            # The FTS table must go first: it references chunks as its
            # external content source.
            fts.drop(connection)

            metadata.drop_all(bind=connection)

        log.info("Creating tables")
        metadata.create_all(bind=connection)

        log.info("Creating FTS5 index '%s'", fts.FTS_TABLE)
        fts.create(connection)

    log.info("Database initialized")


def main() -> None:
    from app.logging_setup import setup_logging

    setup_logging()

    parser = ArgumentParser()

    parser.add_argument(
        "--reset",
        action="store_true",
        help="Drop all existing tables before recreating them.",
    )

    args = parser.parse_args()

    create_database(reset=args.reset)


if __name__ == "__main__":
    main()
