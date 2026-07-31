import argparse

from app.embedding.index import EmbeddingIndexer
from app.logging_setup import setup_logging


def main() -> None:

    setup_logging("embed")

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Recreate the Qdrant collection and re-embed every chunk.",
    )

    args = parser.parse_args()

    EmbeddingIndexer(rebuild=args.rebuild).run()


if __name__ == "__main__":
    main()
