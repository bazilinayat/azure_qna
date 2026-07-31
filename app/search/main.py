"""Interactive CLI for inspecting retrieval results.

Like app/pipeline.py, nothing from `app.*` is imported at module level so that
--chunk-size can select which built index to query before app.config freezes its
settings. Without that, comparing two chunk profiles would mean editing .env
between every query.
"""

import argparse
import os
import time

SEPARATOR = "=" * 100


def print_result(index: int, result, preview: int) -> None:

    print(SEPARATOR)
    print(f"{index}. {result.title}")
    print(f"   {result.url}")
    print(f"   category={result.category}  doc={result.document_id}  chunk={result.chunk_index}")
    print(f"   source={result.source}  score={result.score:.4f}")

    if result.header_path:
        print(f"   path={result.header_path}")

    print()

    content = result.content

    if preview and len(content) > preview:
        content = content[:preview] + " ..."

    print(content)
    print()


def main() -> None:

    parser = argparse.ArgumentParser(
        description="Query the AzureMentor hybrid index.",
    )

    parser.add_argument(
        "--chunk-size",
        type=int,
        metavar="TOKENS",
        help=(
            "Query the index built at this chunk size. Must match a size you "
            "have already built with the pipeline."
        ),
    )

    parser.add_argument("--no-rerank", action="store_true")
    parser.add_argument("--no-expand", action="store_true")
    parser.add_argument("--no-keyword", action="store_true")
    parser.add_argument("--no-vector", action="store_true")

    parser.add_argument("--limit", type=int, default=5)

    parser.add_argument(
        "--preview",
        type=int,
        default=600,
        help="Characters of chunk text to print. 0 prints the whole chunk.",
    )

    parser.add_argument(
        "query",
        nargs="*",
        help="Run one query and exit. Omit for an interactive session.",
    )

    args = parser.parse_args()

    # Before any app.config import; see the module docstring.
    if args.chunk_size is not None:
        os.environ["CHUNK_MAX_TOKENS"] = str(args.chunk_size)

    from app.config import DATABASE_PATH, QDRANT_COLLECTION
    from app.logging_setup import setup_logging
    from app.search.hybrid_search import HybridSearch
    from app.search.search_config import SearchConfig

    setup_logging("search")

    if not DATABASE_PATH.exists():
        raise SystemExit(
            f"No index found at {DATABASE_PATH}.\n"
            f"Build it first:  uv run python -m app.pipeline --fresh"
            + (f" --chunk-size {args.chunk_size}" if args.chunk_size else "")
        )

    print(f"\nIndex: {DATABASE_PATH.name}  |  collection: {QDRANT_COLLECTION}")

    config = SearchConfig(
        final_limit=args.limit,
        enable_reranking=not args.no_rerank,
        enable_query_expansion=not args.no_expand,
        enable_keyword_search=not args.no_keyword,
        enable_vector_search=not args.no_vector,
    )

    search = HybridSearch(config)

    def run(query: str) -> None:
        started = time.perf_counter()

        results = search.search(query)

        elapsed = time.perf_counter() - started

        print(f"\n{len(results)} results in {elapsed:.3f}s\n")

        if not results:
            print("No results found.\n")
            return

        for index, result in enumerate(results, start=1):
            print_result(index, result, args.preview)

    if args.query:
        run(" ".join(args.query))
        return

    print("\nAzureMentor hybrid search. Type 'exit' to quit.\n")

    while True:
        try:
            query = input("Question > ").strip()

        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not query:
            continue

        if query.lower() in {"exit", "quit"}:
            break

        run(query)


if __name__ == "__main__":
    main()
