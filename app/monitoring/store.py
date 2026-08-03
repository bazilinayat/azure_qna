"""Reading and writing monitoring data."""

from datetime import datetime, timezone
from functools import lru_cache
import json
import logging
import uuid

from sqlalchemy import create_engine, event, func, insert, select

from app.config import (
    MONITORING_DATABASE_PATH,
    MONITORING_DATABASE_URL,
    MONITORING_ENABLED,
)
from app.monitoring.schema import conversations, feedback, metadata

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_engine():
    """Engine for the monitoring database, created on first use."""

    MONITORING_DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(MONITORING_DATABASE_URL, future=True)

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()

        # WAL matters more here than for the index: Grafana reads this file
        # while the app writes to it, and the default rollback journal would
        # make readers and writers block each other.
        cursor.execute("PRAGMA journal_mode = WAL")
        cursor.execute("PRAGMA synchronous = NORMAL")
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    metadata.create_all(bind=engine)

    return engine


def init_database() -> None:
    """Create the monitoring tables if they do not exist."""

    get_engine()

    log.info("Monitoring database ready at %s", MONITORING_DATABASE_PATH)


def _now() -> tuple[datetime, int]:
    moment = datetime.now(timezone.utc)

    return moment, int(moment.timestamp())


# --------------------------------------------------
# Writes
# --------------------------------------------------

def log_conversation(
    result,
    session_id: str,
    judgement=None,
    conversation_id: str | None = None,
) -> str | None:
    """Persist one answered question. Returns its id.

    `result` is an app.llm.rag.AnswerResult; `judgement` an optional
    app.eval.judge.Judgement.

    Monitoring must never take down the thing it monitors, so a failure here is
    logged and swallowed rather than raised into the user's chat session.
    """

    if not MONITORING_ENABLED:
        return None

    created_at, created_at_unix = _now()

    row = {
        "id": conversation_id or str(uuid.uuid4()),
        "session_id": session_id,
        "question": result.question,
        "answer": result.answer,
        "prompt_name": result.prompt_name,
        "retrieval_seconds": result.retrieval_seconds,
        "total_seconds": result.total_seconds,
        "num_sources_retrieved": len(result.sources),
        "num_sources_cited": len(result.cited_indices()),
        "sources": json.dumps(
            [
                {
                    "n": index,
                    "title": result.sources[index - 1].title,
                    "url": result.sources[index - 1].url,
                }
                for index in result.cited_indices()
            ]
        ),
        "created_at": created_at,
        "created_at_unix": created_at_unix,
    }

    from app.config import CHUNK_PROFILE, EMBEDDING_MODEL

    row["chunk_profile"] = CHUNK_PROFILE
    row["embedding_model"] = EMBEDDING_MODEL

    llm = result.llm_response

    if llm is not None:
        row.update(
            model=llm.model,
            generation_seconds=llm.latency_seconds,
            prompt_tokens=llm.prompt_tokens,
            completion_tokens=llm.completion_tokens,
            reasoning_tokens=llm.reasoning_tokens,
            total_tokens=llm.total_tokens,
            cost_usd=llm.cost_usd,
        )
    else:
        # The refusal path: no model was called at all.
        row.update(model="none", generation_seconds=0.0)

    if judgement is not None:
        row.update(
            relevance=judgement.relevance,
            relevance_explanation=judgement.explanation,
            judge_model=judgement.model,
            judge_tokens=judgement.total_tokens,
            judge_cost_usd=judgement.cost_usd,
        )

    try:
        with get_engine().begin() as connection:
            connection.execute(insert(conversations), row)

    except Exception:
        log.exception("Failed to log conversation; continuing")
        return None

    return row["id"]


def log_feedback(conversation_id: str, value: int) -> bool:
    """Record a thumbs up (+1) or down (-1)."""

    if not MONITORING_ENABLED:
        return False

    if value not in (1, -1):
        raise ValueError(f"Feedback must be +1 or -1, got {value!r}")

    created_at, created_at_unix = _now()

    try:
        with get_engine().begin() as connection:
            connection.execute(
                insert(feedback),
                {
                    "conversation_id": conversation_id,
                    "value": value,
                    "created_at": created_at,
                    "created_at_unix": created_at_unix,
                },
            )

    except Exception:
        log.exception("Failed to log feedback")
        return False

    return True


# --------------------------------------------------
# Reads
# --------------------------------------------------

def summary() -> dict:
    """Headline numbers, used to sanity-check that logging works."""

    with get_engine().connect() as connection:

        total = connection.execute(
            select(func.count()).select_from(conversations)
        ).scalar_one()

        if not total:
            return {"conversations": 0}

        row = connection.execute(
            select(
                func.avg(conversations.c.total_seconds),
                func.sum(conversations.c.total_tokens),
                func.sum(conversations.c.cost_usd),
                func.count(func.distinct(conversations.c.session_id)),
            )
        ).one()

        relevance = dict(
            connection.execute(
                select(
                    conversations.c.relevance,
                    func.count(),
                )
                .where(conversations.c.relevance.is_not(None))
                .group_by(conversations.c.relevance)
            ).all()
        )

        votes = dict(
            connection.execute(
                select(feedback.c.value, func.count()).group_by(
                    feedback.c.value
                )
            ).all()
        )

    return {
        "conversations": total,
        "sessions": row[3],
        "avg_seconds": round(row[0] or 0, 2),
        "total_tokens": row[1] or 0,
        "total_cost_usd": row[2],
        "relevance": relevance,
        "thumbs_up": votes.get(1, 0),
        "thumbs_down": votes.get(-1, 0),
    }


def recent(limit: int = 20) -> list[dict]:
    """Most recent conversations, newest first."""

    with get_engine().connect() as connection:
        rows = connection.execute(
            select(
                conversations.c.id,
                conversations.c.created_at,
                conversations.c.question,
                conversations.c.relevance,
                conversations.c.total_seconds,
                conversations.c.total_tokens,
                conversations.c.cost_usd,
            )
            .order_by(conversations.c.created_at_unix.desc())
            .limit(limit)
        ).all()

    return [dict(row._mapping) for row in rows]
