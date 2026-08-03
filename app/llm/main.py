"""Ask AzureMentor a question from the command line.

    uv run python -m app.llm.main "how do I make a blob container private"
    uv run python -m app.llm.main --prompt concise --chunk-size 256

As with the other entry points, app.* imports are deferred until after the CLI
flags have been turned into environment variables, so --chunk-size can pick which
built index to answer from.
"""

import argparse
import os


def format_cost(cost: float | None) -> str:
    if cost is None:
        return "unknown (set LLM_PRICE_INPUT_PER_1M / LLM_PRICE_OUTPUT_PER_1M)"

    return f"${cost:.6f}"


def print_answer(result, show_sources: bool, show_stats: bool) -> None:

    print()
    print(result.answer)
    print()

    if show_sources and result.sources:
        cited = result.cited_indices()

        print("-" * 80)
        print(
            f"Sources cited ({len(cited)} of {len(result.sources)} retrieved)"
            if cited
            else f"No sources cited ({len(result.sources)} retrieved, none used)"
        )
        print("-" * 80)

        for index in cited:
            source = result.sources[index - 1]

            print(f"[{index}] {source.title}")

            # The breadcrumb starts with the title, so for a chunk from the top
            # of an article it is identical and printing it just repeats.
            if source.header_path and source.header_path != source.title:
                print(f"    {source.header_path}")

            print(f"    {source.url}")

        print()

    if not show_stats:
        return

    print("-" * 80)

    llm = result.llm_response

    if llm is None:
        print(f"retrieval {result.retrieval_seconds:.2f}s  |  no LLM call made")
        print()
        return

    print(
        f"retrieval {result.retrieval_seconds:.2f}s  "
        f"generation {llm.latency_seconds:.2f}s  "
        f"total {result.total_seconds:.2f}s"
    )
    print(
        f"tokens: {llm.prompt_tokens} in + {llm.completion_tokens} out "
        f"({llm.reasoning_tokens} reasoning) = {llm.total_tokens}"
    )
    print(f"model: {llm.model}  |  prompt: {result.prompt_name}")
    print(f"cost: {format_cost(llm.cost_usd)}")
    print()


def main() -> None:

    parser = argparse.ArgumentParser(
        description="Ask AzureMentor a question.",
    )

    parser.add_argument(
        "--chunk-size",
        type=int,
        metavar="TOKENS",
        help="Answer from the index built at this chunk size.",
    )

    parser.add_argument(
        "--prompt",
        help="Prompt template name. See app/llm/prompts.py.",
    )

    parser.add_argument(
        "--model",
        help="Override the OpenAI model for this run.",
    )

    parser.add_argument(
        "--chunks",
        type=int,
        help="How many retrieved chunks to pass as context.",
    )

    parser.add_argument(
        "--no-sources",
        action="store_true",
        help="Hide the source list.",
    )

    parser.add_argument(
        "--no-stats",
        action="store_true",
        help="Hide timing, token and cost lines.",
    )

    parser.add_argument(
        "question",
        nargs="*",
        help="Ask one question and exit. Omit for an interactive session.",
    )

    args = parser.parse_args()

    # Must precede the first app.config import.
    if args.chunk_size is not None:
        os.environ["CHUNK_MAX_TOKENS"] = str(args.chunk_size)

    if args.model:
        os.environ["LLM_MODEL"] = args.model

    from app.config import DATABASE_PATH, LLM_MODEL, QDRANT_COLLECTION
    from app.llm.prompts import PROMPTS
    from app.llm.rag import RagPipeline
    from app.logging_setup import setup_logging

    setup_logging("ask")

    if not DATABASE_PATH.exists():
        raise SystemExit(
            f"No index found at {DATABASE_PATH}.\n"
            f"Build one first:  uv run python -m app.pipeline --fresh"
            + (f" --chunk-size {args.chunk_size}" if args.chunk_size else "")
        )

    if args.prompt and args.prompt not in PROMPTS:
        raise SystemExit(
            f"Unknown prompt {args.prompt!r}. Available: {', '.join(PROMPTS)}"
        )

    pipeline_kwargs = {}

    if args.prompt:
        pipeline_kwargs["prompt"] = args.prompt

    if args.chunks:
        pipeline_kwargs["context_chunks"] = args.chunks

    pipeline = RagPipeline(**pipeline_kwargs)

    print(
        f"\nAzureMentor  |  {LLM_MODEL}  |  {QDRANT_COLLECTION}  "
        f"|  prompt: {pipeline.prompt.name}"
    )

    def ask(question: str) -> None:
        try:
            result = pipeline.answer(question)

        except Exception as exc:
            print(f"\nFailed to answer: {type(exc).__name__}: {exc}\n")
            return

        print_answer(result, not args.no_sources, not args.no_stats)

    if args.question:
        ask(" ".join(args.question))
        return

    print("Type a question, or 'exit' to quit.\n")

    while True:
        try:
            question = input("You > ").strip()

        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not question:
            continue

        if question.lower() in {"exit", "quit"}:
            break

        ask(question)


if __name__ == "__main__":
    main()
