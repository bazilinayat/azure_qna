"""Evaluation CLI.

    uv run python -m app.eval.main generate --sample 150
    uv run python -m app.eval.main retrieval
    uv run python -m app.eval.main answers --sample 30
    uv run python -m app.eval.main live

`generate` and `answers` call the OpenAI API and cost money; `retrieval` and
`live` do not.

As with the other entry points, app.* imports are deferred so --chunk-size can
select which index to evaluate before config freezes.
"""

import argparse
import csv
import json
import os
from datetime import datetime


def _write_results(rows: list[dict], name: str, results_dir, quiet: bool = False):
    """Persist results as CSV so runs can be compared over time.

    Returns the path written, or None if there was nothing to write.
    """

    if not rows:
        return None

    results_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = results_dir / f"{name}-{stamp}.csv"

    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    if not quiet:
        print(f"\nSaved to {path}")

    return path


# --------------------------------------------------
# Commands
# --------------------------------------------------

def command_generate(args) -> int:
    from app.config import GROUND_TRUTH_PATH
    from app.eval import ground_truth

    items = ground_truth.generate(
        sample_size=args.sample,
        questions_per_document=args.per_document,
        seed=args.seed,
    )

    if not items:
        print("No questions were generated.")
        return 1

    ground_truth.save(items)

    print(f"\nGenerated {len(items)} questions -> {GROUND_TRUTH_PATH}")
    print("\nA few examples:")

    for item in items[:5]:
        print(f"  - {item.question}")
        print(f"      expects: {item.title}")

    return 0


def command_retrieval(args) -> int:
    from app.config import EVAL_RESULTS_DIR
    from app.eval import ground_truth, retrieval

    items = ground_truth.load()

    if args.sample and args.sample < len(items):
        import random

        random.seed(args.seed)
        items = random.sample(items, args.sample)

    configs = retrieval.standard_configs()

    if args.configs:
        wanted = [name.strip() for name in args.configs.split(",") if name.strip()]

        unknown = [name for name in wanted if name not in configs]

        if unknown:
            print(f"Unknown configuration(s): {', '.join(unknown)}")
            print(f"Available: {', '.join(configs)}")
            return 1

        configs = {name: configs[name] for name in wanted}

    print(f"Evaluating {len(configs)} configuration(s) over {len(items)} questions\n")

    # A full sweep runs for tens of minutes and the reranking configurations are
    # memory-hungry. Writing after every configuration means a crash near the end
    # no longer discards everything that already succeeded.
    def save_progress(partial):
        _write_results(
            [result.as_row() for result in partial],
            "retrieval",
            EVAL_RESULTS_DIR,
            quiet=True,
        )

    results = retrieval.sweep(items, configs=configs, on_result=save_progress)

    if not results:
        print("No configuration completed successfully.")
        return 1

    print("\n" + retrieval.format_table(results))

    _write_results(
        [result.as_row() for result in results],
        "retrieval",
        EVAL_RESULTS_DIR,
    )

    if len(results) < len(configs):
        failed = set(configs) - {result.name for result in results}
        print(f"\nDid not complete: {', '.join(sorted(failed))}")
        return 2

    return 0


def command_answers(args) -> int:
    from app.config import EVAL_RESULTS_DIR
    from app.eval import answers, ground_truth

    items = ground_truth.load()

    prompts = args.prompts.split(",") if args.prompts else None

    results = answers.sweep_prompts(
        items,
        prompt_names=prompts,
        sample_size=args.sample,
        seed=args.seed,
    )

    print("\n" + answers.format_table(results))

    _write_results(
        [result.as_row() for result in results],
        "answers",
        EVAL_RESULTS_DIR,
    )

    if args.show_failures:
        print("\nNon-relevant answers:\n")

        for result in results:
            for evaluation in result.evaluations:
                if evaluation.judgement.relevance == "NON_RELEVANT":
                    print(f"[{result.name}] {evaluation.question}")
                    print(f"    judge: {evaluation.judgement.explanation}")
                    print(f"    retrieved expected doc: {evaluation.retrieved_expected}")
                    print()

    return 0


def command_live(args) -> int:
    """Summarise the relevance of real answers already logged by the app."""

    from app.monitoring import store

    data = store.summary()

    if not data.get("conversations"):
        print(
            "No conversations logged yet. Ask something in the Streamlit app "
            "first:\n  uv run streamlit run app/ui/streamlit_app.py"
        )
        return 0

    print(json.dumps(data, indent=2, default=str))

    print("\nMost recent:\n")

    for row in store.recent(args.limit):
        relevance = row["relevance"] or "unjudged"

        print(f"  {row['created_at']}  {relevance:<16} {row['question'][:60]}")

    return 0


# --------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="AzureMentor evaluation.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "--chunk-size",
        type=int,
        metavar="TOKENS",
        help="Evaluate the index built at this chunk size.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser(
        "generate", help="Generate synthetic ground truth (uses the API)."
    )
    generate.add_argument("--sample", type=int, default=150,
                          help="Documents to sample. Default 150.")
    generate.add_argument("--per-document", type=int, default=3,
                          help="Questions per document. Default 3.")
    generate.add_argument("--seed", type=int, default=42)
    generate.set_defaults(handler=command_generate)

    retrieval_parser = subparsers.add_parser(
        "retrieval", help="Hit rate and MRR sweep. No API calls."
    )
    retrieval_parser.add_argument("--sample", type=int, default=None,
                                  help="Use only N questions.")
    retrieval_parser.add_argument("--configs",
                                  help="Comma-separated configuration names, "
                                       "to rerun a subset after a failure.")
    retrieval_parser.add_argument("--seed", type=int, default=42)
    retrieval_parser.set_defaults(handler=command_retrieval)

    answers_parser = subparsers.add_parser(
        "answers", help="Judge answer quality per prompt (uses the API)."
    )
    answers_parser.add_argument("--sample", type=int, default=30,
                                help="Questions per prompt. Default 30.")
    answers_parser.add_argument("--prompts",
                                help="Comma-separated prompt names.")
    answers_parser.add_argument("--seed", type=int, default=42)
    answers_parser.add_argument("--show-failures", action="store_true",
                                help="Print every non-relevant answer.")
    answers_parser.set_defaults(handler=command_answers)

    live = subparsers.add_parser(
        "live", help="Summarise relevance of logged live traffic."
    )
    live.add_argument("--limit", type=int, default=20)
    live.set_defaults(handler=command_live)

    args = parser.parse_args()

    if args.chunk_size is not None:
        os.environ["CHUNK_MAX_TOKENS"] = str(args.chunk_size)

    from app.logging_setup import setup_logging

    setup_logging("eval")

    raise SystemExit(args.handler(args))


if __name__ == "__main__":
    main()
