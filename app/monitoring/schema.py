"""Monitoring tables.

These live in their own database file, not the index. The index is rebuilt by
`app.pipeline --fresh` and is namespaced per chunk profile; conversations and
user feedback have to outlive both.

Every column here exists because a Grafana panel needs it. `created_at_unix` is
duplicated alongside `created_at` because the SQLite datasource plugin wants an
integer epoch for time-series panels, and converting an ISO string in every
query is both slower and easy to get wrong.
"""

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    func,
)

metadata = MetaData()

# --------------------------------------------------
# Conversations
# --------------------------------------------------

conversations = Table(
    "conversations",
    metadata,

    # UUID string rather than an autoincrement integer: the Streamlit app needs
    # the id to attach feedback to before the row is any use to anyone else.
    Column("id", String(36), primary_key=True),

    # Groups turns from one browser session. Chat history is session-only by
    # design, but the rows still need to be groupable for the dashboard.
    Column("session_id", String(36), nullable=False),

    Column("question", Text, nullable=False),
    Column("answer", Text, nullable=False),

    # --- configuration that produced this answer ---
    Column("model", String(100), nullable=False),
    Column("prompt_name", String(100), nullable=False),
    Column("chunk_profile", String(20)),
    Column("embedding_model", String(100)),

    # --- latency ---
    Column("retrieval_seconds", Float, nullable=False, default=0.0),
    Column("generation_seconds", Float, nullable=False, default=0.0),
    Column("total_seconds", Float, nullable=False, default=0.0),

    # --- tokens and cost ---
    Column("prompt_tokens", Integer, nullable=False, default=0),
    Column("completion_tokens", Integer, nullable=False, default=0),
    Column("reasoning_tokens", Integer, nullable=False, default=0),
    Column("total_tokens", Integer, nullable=False, default=0),

    # Nullable: unknown for models without a price in LLM_PRICING, and an
    # honest NULL beats a fabricated number on a cost dashboard.
    Column("cost_usd", Float),

    # --- retrieval ---
    Column("num_sources_retrieved", Integer, nullable=False, default=0),
    Column("num_sources_cited", Integer, nullable=False, default=0),

    # JSON array of the cited sources, for drilling into a specific answer.
    Column("sources", Text),

    # --- automatic relevance judgement ---
    Column("relevance", String(20)),
    Column("relevance_explanation", Text),
    Column("judge_model", String(100)),
    Column("judge_tokens", Integer),
    Column("judge_cost_usd", Float),

    Column(
        "created_at",
        DateTime,
        server_default=func.now(),
        nullable=False,
    ),

    # Epoch seconds, for Grafana time-series panels.
    Column("created_at_unix", Integer, nullable=False),
)

# --------------------------------------------------
# Feedback
# --------------------------------------------------

feedback = Table(
    "feedback",
    metadata,

    Column("id", Integer, primary_key=True, autoincrement=True),

    Column(
        "conversation_id",
        String(36),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    ),

    # +1 thumbs up, -1 thumbs down. An integer so Grafana can simply sum it.
    Column("value", Integer, nullable=False),

    Column(
        "created_at",
        DateTime,
        server_default=func.now(),
        nullable=False,
    ),

    Column("created_at_unix", Integer, nullable=False),
)

# --------------------------------------------------
# Indexes
# --------------------------------------------------

# Nearly every dashboard panel filters or groups by time.
Index("idx_conversations_created", conversations.c.created_at_unix)

Index("idx_conversations_session", conversations.c.session_id)
Index("idx_conversations_relevance", conversations.c.relevance)
Index("idx_conversations_model", conversations.c.model)

Index("idx_feedback_conversation", feedback.c.conversation_id)
Index("idx_feedback_created", feedback.c.created_at_unix)
