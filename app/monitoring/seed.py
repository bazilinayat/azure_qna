"""Generate traffic so the dashboard has something to show.

Two modes, because they trade off differently:

    real       Asks questions through the actual pipeline. Everything logged is
               genuine -- real latency, real tokens, real judge verdicts. Costs
               API calls (roughly 2 per question) and takes ~8s per question.

    synthetic  Writes plausible rows straight into the monitoring database, with
               no API calls at all. Free and instant, and it can spread rows
               across past days so the time-series panels have a shape. But the
               numbers are invented.

Synthetic rows are tagged with a `synthetic-` session id so they can be told
apart and removed. Do not present synthetic data as real traffic; use `real` for
anything that goes in a report or a screenshot you are claiming is live usage.

    uv run python -m app.monitoring.seed --questions 25
    uv run python -m app.monitoring.seed --synthetic --questions 200 --days 7
    uv run python -m app.monitoring.seed --clear-synthetic
"""

from datetime import datetime, timedelta, timezone
import argparse
import json
import logging
import os
import random
import uuid

log = logging.getLogger(__name__)

SYNTHETIC_PREFIX = "synthetic-"

# Real questions spanning the indexed services, so retrieval actually has
# something to find. The last few are deliberately outside the corpus -- an
# honest dashboard needs some NON_RELEVANT answers, and a relevance pie chart
# that is 100% green is a sign the questions were cherry-picked.
QUESTIONS = [
    # storage
    "How do I stop a blob container from being publicly readable?",
    "What are the blob access tiers and when should I use each?",
    "How do I enable soft delete for blobs?",
    "What is the difference between a block blob and a page blob?",
    "How do I generate a shared access signature for a container?",
    "How do I set up lifecycle management to archive old blobs?",
    # app service
    "How do I deploy a Python app to Azure App Service?",
    "What is an App Service plan and how does pricing work?",
    "How do I configure a custom domain with TLS on App Service?",
    "How do I set environment variables for an App Service app?",
    "What are deployment slots and how do I swap them?",
    # functions
    "How do I create a timer-triggered Azure Function?",
    "What is the difference between the consumption and premium plans?",
    "How do I connect a Function App to storage without connection strings?",
    "How do I run Azure Functions locally?",
    # networking
    "How do I create a virtual network and subnet?",
    "What is a private endpoint and when should I use one?",
    "How do I restrict storage account access to a virtual network?",
    # identity and governance
    "What is the difference between a role assignment and a role definition?",
    "How do I assign the Reader role to a user at resource group scope?",
    "What is Azure Policy and how does it differ from RBAC?",
    "How do I use managed identity to access a storage account?",
    # cost
    "How do I set a budget alert on my subscription?",
    "How can I see which resources are costing the most?",
    # messaging and integration
    "What is the difference between Event Grid and Service Bus?",
    "How do I create a Service Bus queue and send a message?",
    "How do I trigger a Logic App from an HTTP request?",
    # ARM / deployment
    "What is an ARM template and how do I deploy one?",
    "How do I use parameters in a Bicep file?",
    "How do I deploy a static web app from GitHub?",
    # deliberately outside the indexed corpus
    "How do I create an AKS cluster with autoscaling?",
    "How do I rotate secrets in Azure Key Vault?",
    "What is the capital of France?",
    "How do I resize a virtual machine?",
]


# --------------------------------------------------
# Real traffic
# --------------------------------------------------

def seed_real(count: int, feedback_rate: float, seed: int) -> int:
    """Ask real questions through the real pipeline and log the results."""

    from app.config import JUDGE_LIVE_ANSWERS
    from app.eval.judge import RelevanceJudge
    from app.llm.rag import RagPipeline
    from app.monitoring.store import log_conversation, log_feedback

    random.seed(seed)

    questions = list(QUESTIONS)
    random.shuffle(questions)

    if count > len(questions):
        # Repeat rather than refuse; repeated questions are realistic traffic.
        questions = (questions * (count // len(questions) + 1))

    questions = questions[:count]

    pipeline = RagPipeline()
    judge = RelevanceJudge() if JUDGE_LIVE_ANSWERS else None

    # A handful of sessions rather than one per question, so the "sessions"
    # count on the dashboard is not simply equal to the question count.
    sessions = [str(uuid.uuid4()) for _ in range(max(1, count // 4))]

    written = 0

    for index, question in enumerate(questions, start=1):

        try:
            result = pipeline.answer(question)

        except Exception:
            log.exception("Failed to answer %r", question)
            continue

        judgement = None

        if judge is not None and result.llm_response is not None:
            try:
                judgement = judge.judge(question, result.answer)
            except Exception:
                log.exception("Judge failed for %r", question)

        conversation_id = log_conversation(
            result,
            session_id=random.choice(sessions),
            judgement=judgement,
        )

        written += 1

        verdict = judgement.relevance if judgement else "unjudged"

        log.info(
            "[%s/%s] %-13s %.1fs  %s",
            index,
            len(questions),
            verdict,
            result.total_seconds,
            question[:58],
        )

        if conversation_id and random.random() < feedback_rate:
            log_feedback(conversation_id, _vote_for(verdict))

    return written


def _vote_for(relevance: str) -> int:
    """A plausible user vote for a given judge verdict.

    Correlated but not identical: users mostly agree with the judge, and the
    cases where they do not are the interesting ones the dashboard surfaces.
    """

    if relevance == "RELEVANT":
        return 1 if random.random() < 0.85 else -1

    if relevance == "PARTLY_RELEVANT":
        return 1 if random.random() < 0.5 else -1

    return 1 if random.random() < 0.1 else -1


# --------------------------------------------------
# Synthetic traffic
# --------------------------------------------------

def seed_synthetic(
    count: int,
    days: int,
    feedback_rate: float,
    seed: int,
) -> int:
    """Write plausible rows directly, with no API calls.

    Distributions are taken from observed real traffic: retrieval around 0.3s
    with reranking off, generation 2-5s, ~2,300 prompt tokens and 150-400
    completion tokens.
    """

    from sqlalchemy import insert

    from app.config import CHUNK_PROFILE, EMBEDDING_MODEL, LLM_MODEL
    from app.llm.client import estimate_cost
    from app.monitoring.schema import conversations, feedback
    from app.monitoring.store import get_engine

    random.seed(seed)

    now = datetime.now(timezone.utc)
    window = timedelta(days=days)

    sessions = [
        f"{SYNTHETIC_PREFIX}{uuid.uuid4()}"
        for _ in range(max(1, count // 5))
    ]

    conversation_rows = []
    feedback_rows = []

    for _ in range(count):

        question = random.choice(QUESTIONS)

        # Questions outside the corpus should mostly fail, which is what makes
        # the relevance chart look like a real system rather than a demo.
        off_corpus = question in QUESTIONS[-4:]

        if off_corpus:
            relevance = random.choices(
                ["NON_RELEVANT", "PARTLY_RELEVANT", "RELEVANT"],
                weights=[70, 25, 5],
            )[0]
        else:
            relevance = random.choices(
                ["RELEVANT", "PARTLY_RELEVANT", "NON_RELEVANT"],
                weights=[75, 20, 5],
            )[0]

        # Weighted towards recent, so the dashboard's default 6-hour window is
        # not empty while the 7-day view still has history.
        age = window * (random.random() ** 2)
        moment = now - age

        retrieval = round(random.uniform(0.18, 0.55), 3)
        generation = round(random.uniform(1.9, 5.4), 3)

        prompt_tokens = random.randint(1900, 2600)
        completion_tokens = random.randint(120, 420)
        total_tokens = prompt_tokens + completion_tokens

        cited = 0 if relevance == "NON_RELEVANT" else random.randint(1, 4)

        conversation_rows.append({
            "id": str(uuid.uuid4()),
            "session_id": random.choice(sessions),
            "question": question,
            "answer": f"[synthetic demo row] Generated answer for: {question}",
            "model": LLM_MODEL,
            "prompt_name": random.choices(
                ["grounded_mentor", "concise"], weights=[85, 15]
            )[0],
            "chunk_profile": CHUNK_PROFILE,
            "embedding_model": EMBEDDING_MODEL,
            "retrieval_seconds": retrieval,
            "generation_seconds": generation,
            "total_seconds": round(retrieval + generation, 3),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "reasoning_tokens": 0,
            "total_tokens": total_tokens,
            "cost_usd": estimate_cost(LLM_MODEL, prompt_tokens, completion_tokens),
            "num_sources_retrieved": 5,
            "num_sources_cited": cited,
            "sources": json.dumps([]),
            "relevance": relevance,
            "relevance_explanation": f"Synthetic verdict: {relevance}.",
            "judge_model": LLM_MODEL,
            "judge_tokens": random.randint(600, 900),
            "judge_cost_usd": None,
            "created_at": moment,
            "created_at_unix": int(moment.timestamp()),
        })

        if random.random() < feedback_rate:
            vote_moment = moment + timedelta(seconds=random.randint(5, 120))

            feedback_rows.append({
                "conversation_id": conversation_rows[-1]["id"],
                "value": _vote_for(relevance),
                "created_at": vote_moment,
                "created_at_unix": int(vote_moment.timestamp()),
            })

    with get_engine().begin() as connection:
        connection.execute(insert(conversations), conversation_rows)

        if feedback_rows:
            connection.execute(insert(feedback), feedback_rows)

    log.info(
        "Inserted %s synthetic conversations and %s votes across %s days",
        len(conversation_rows),
        len(feedback_rows),
        days,
    )

    return len(conversation_rows)


def clear_synthetic() -> int:
    """Delete every synthetic row, leaving real traffic untouched."""

    from sqlalchemy import delete, select

    from app.monitoring.schema import conversations, feedback
    from app.monitoring.store import get_engine

    with get_engine().begin() as connection:

        ids = [
            row[0]
            for row in connection.execute(
                select(conversations.c.id).where(
                    conversations.c.session_id.like(f"{SYNTHETIC_PREFIX}%")
                )
            ).all()
        ]

        if not ids:
            return 0

        connection.execute(
            delete(feedback).where(feedback.c.conversation_id.in_(ids))
        )

        connection.execute(
            delete(conversations).where(conversations.c.id.in_(ids))
        )

    return len(ids)


# --------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate dashboard traffic.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "--questions", type=int, default=25,
        help="How many questions to generate. Default 25.",
    )

    parser.add_argument(
        "--synthetic", action="store_true",
        help="Write plausible rows directly instead of calling the API. Free.",
    )

    parser.add_argument(
        "--days", type=int, default=7,
        help="Synthetic only: spread rows over this many past days. Default 7.",
    )

    parser.add_argument(
        "--feedback-rate", type=float, default=0.4,
        help="Fraction of answers that get a thumbs vote. Default 0.4.",
    )

    parser.add_argument(
        "--clear-synthetic", action="store_true",
        help="Delete all synthetic rows and exit. Real traffic is untouched.",
    )

    parser.add_argument(
        "--chunk-size", type=int, metavar="TOKENS",
        help="Use the index built at this chunk size.",
    )

    args = parser.parse_args()

    if args.chunk_size is not None:
        os.environ["CHUNK_MAX_TOKENS"] = str(args.chunk_size)

    from app.logging_setup import setup_logging
    from app.monitoring.store import init_database, summary

    setup_logging("seed")
    init_database()

    if args.clear_synthetic:
        removed = clear_synthetic()
        print(f"\nRemoved {removed} synthetic conversations.")
        print(json.dumps(summary(), indent=2, default=str))
        return

    if args.synthetic:
        seed_synthetic(
            args.questions, args.days, args.feedback_rate, seed=42
        )
    else:
        from app.config import DATABASE_PATH

        if not DATABASE_PATH.exists():
            raise SystemExit(
                f"No search index at {DATABASE_PATH}.\n"
                f"Build one first:  uv run python -m app.pipeline --fresh\n"
                f"Or generate rows without it:  "
                f"uv run python -m app.monitoring.seed --synthetic"
            )

        log.info(
            "Asking %s real questions (about %s API calls). Ctrl-C to stop; "
            "everything logged so far is kept.",
            args.questions,
            args.questions * 2,
        )

        seed_real(args.questions, args.feedback_rate, seed=42)

    print()
    print(json.dumps(summary(), indent=2, default=str))
    print("\nDashboard: http://localhost:3000/d/azurementor-monitoring")


if __name__ == "__main__":
    main()
