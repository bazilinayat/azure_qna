"""Central configuration for AzureMentor.

Every value can be overridden through the environment (see .env.example).
"""

from pathlib import Path
import os

from dotenv import load_dotenv

load_dotenv()


def _env_int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)

    if raw is None:
        return default

    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_str(name: str, default: str) -> str:
    """Read a string setting, treating a blank value as unset.

    `os.getenv(name, default)` returns "" for `NAME=` in a .env file, which would
    silently produce an empty database path or collection name.
    """

    value = os.getenv(name)

    return value.strip() if value and value.strip() else default


def _env_list(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name)

    if raw is None:
        return default

    return [
        item.strip()
        for item in raw.split(",")
        if item.strip()
    ]


# --------------------------------------------------
# Paths
# --------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "data"

SOURCE_DIR = DATA_DIR / "source"

LOG_DIR = BASE_DIR / "logs"

# Storage paths are defined further down, because they are derived from the
# chunk size so that different chunking experiments cannot overwrite each other.

# --------------------------------------------------
# Source documentation repositories
# --------------------------------------------------
#
# Microsoft split the old monolithic azure-docs repo: virtual machines, AKS,
# Key Vault, Cosmos DB, Monitor and Entra ID now live in separate repositories.
# Add them here as separate entries once their names are confirmed.

SOURCE_REPOS: list[dict[str, str]] = [
    {
        "name": "azure-docs",
        "url": "https://github.com/MicrosoftDocs/azure-docs.git",
        "articles_dir": "articles",
        "url_base": "https://learn.microsoft.com/azure/",
    },
]

# --------------------------------------------------
# Ingestion scope
# --------------------------------------------------
#
# The full azure-docs corpus is ~13,500 standalone articles across 145 service
# folders, which is roughly 95,000 chunks and several hours of embedding on CPU.
# Narrowing to core services keeps the index small, iteration fast and retrieval
# sharper — there are far fewer near-duplicate boilerplate pages competing for
# the top-k slots.
#
# This default is the set a beginner-to-intermediate Azure learner actually
# asks about: compute, storage, networking basics, identity and cost. Widen it by
# setting INGEST_CATEGORIES, or set INGEST_ALL_CATEGORIES=true for everything.

INGEST_ALL_CATEGORIES = _env_bool("INGEST_ALL_CATEGORIES", False)

INGEST_CATEGORIES = _env_list(
    "INGEST_CATEGORIES",
    [
        "app-service",
        "azure-functions",
        "container-apps",
        "storage",
        "virtual-network",
        "azure-resource-manager",
        "role-based-access-control",
        "cost-management-billing",
        "governance",
        "security",
        "api-management",
        "logic-apps",
        "event-grid",
        "service-bus-messaging",
        "static-web-apps",
    ],
)

# Articles under an "includes" folder are reusable fragments, not standalone
# documents. Indexing them produces headerless, contextless chunks.
SKIP_INCLUDE_FILES = _env_bool("SKIP_INCLUDE_FILES", True)

# Documents shorter than this (in tokens, after cleaning) are stubs / redirects.
MIN_DOC_TOKENS = _env_int("MIN_DOC_TOKENS", 80)

# --------------------------------------------------
# Embedding model
# --------------------------------------------------

EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")

# "torch" or "fastembed". torch is ~4x faster here because fastembed ships the
# int8-quantized ONNX build, which is slower than fp32 on this CPU. See the
# module docstring in app/embedding/embedder.py for the measurements.
EMBEDDING_BACKEND = os.getenv("EMBEDDING_BACKEND", "torch")

# Hard truncation limit of the embedding model. Anything longer is silently
# discarded by the encoder, so it is the ceiling every chunk must respect.
EMBEDDING_MAX_TOKENS = _env_int("EMBEDDING_MAX_TOKENS", 512)

# --------------------------------------------------
# Chunking  --  the one knob for retrieval experiments
# --------------------------------------------------
#
# CHUNK_MAX_TOKENS is THE variable to sweep when evaluating retrieval. Set it and
# nothing else: the overlap, the minimum chunk size, the database file and the
# Qdrant collection all derive from it, so two chunk sizes cannot overwrite each
# other's index and can be compared without rebuilding.
#
#     uv run python -m app.pipeline --fresh --chunk-size 256
#
# It is measured with the *embedding model's* tokenizer, not tiktoken.
# bge-small-en-v1.5 truncates at 512, and its WordPiece tokenizer emits ~1.27x
# more tokens than cl100k_base on Azure docs, so chunking to "512 tiktoken
# tokens" would silently discard the tail of every chunk. The default of 480
# leaves room for [CLS]/[SEP] plus the prepended header path.

CHUNK_MAX_TOKENS = _env_int("CHUNK_MAX_TOKENS", 480)

# Guard rails. A chunk over the model limit is truncated with no error at all,
# which is the single easiest way to silently ruin retrieval quality.
if CHUNK_MAX_TOKENS > EMBEDDING_MAX_TOKENS:
    raise ValueError(
        f"CHUNK_MAX_TOKENS ({CHUNK_MAX_TOKENS}) exceeds the embedding model's "
        f"limit of {EMBEDDING_MAX_TOKENS} tokens. Chunks that long are silently "
        f"truncated by the encoder. Lower it, or switch to a long-context "
        f"embedding model and raise EMBEDDING_MAX_TOKENS to match."
    )

if CHUNK_MAX_TOKENS < 64:
    raise ValueError(
        f"CHUNK_MAX_TOKENS ({CHUNK_MAX_TOKENS}) is too small to be useful. The "
        f"header breadcrumb alone would consume most of the budget."
    )

# Derived so that one knob stays one knob. An overlap that does not scale with
# the chunk size silently becomes a huge proportion of it: leaving the 480-token
# default of 64 in place while testing 128-token chunks would mean 50% overlap
# and a corpus half made of duplicates. Both can still be set explicitly.

CHUNK_OVERLAP_TOKENS = _env_int("CHUNK_OVERLAP_TOKENS", CHUNK_MAX_TOKENS // 8)

# Chunks below this get merged into their neighbour rather than stored alone,
# so single-line sections don't become their own useless chunk.
CHUNK_MIN_TOKENS = _env_int("CHUNK_MIN_TOKENS", CHUNK_MAX_TOKENS // 16)

# Identifies everything this chunk configuration produces.
CHUNK_PROFILE = f"c{CHUNK_MAX_TOKENS}"

# --------------------------------------------------
# Storage  --  namespaced per chunk profile
# --------------------------------------------------
#
# Both the database file and the Qdrant collection carry the chunk profile, so
# running a second chunk size does not destroy the first. Switching between
# finished experiments is then just a matter of passing --chunk-size again;
# no rebuild required.

DATABASE_PATH = Path(
    _env_str("DATABASE_PATH", str(DATA_DIR / f"azurementor-{CHUNK_PROFILE}.db"))
)

DATABASE_URL = _env_str("DATABASE_URL", f"sqlite:///{DATABASE_PATH}")

# Pinning either of these in .env defeats the per-profile isolation: every chunk
# size would write to the same database or collection, and a comparison between
# two sizes would quietly be a comparison of one size against itself. The
# pipeline warns when this is the case rather than letting it pass unnoticed.
STORAGE_PATH_IS_PINNED = bool(
    _env_str("DATABASE_PATH", "") or _env_str("DATABASE_URL", "")
)

COLLECTION_IS_PINNED = bool(_env_str("QDRANT_COLLECTION", ""))

# Rows pulled from SQLite and embedded per iteration. Also the resume
# granularity: a crash loses at most this many chunks of work.
EMBEDDING_BATCH_SIZE = _env_int("EMBEDDING_BATCH_SIZE", 512)

# Inner batch handed to the model.
EMBEDDING_MODEL_BATCH_SIZE = _env_int("EMBEDDING_MODEL_BATCH_SIZE", 32)

# Throughput stops improving past ~8 threads on this workload (measured 5.2 /
# 8.1 / 8.3 chunks/s at 4 / 8 / 16 threads), so more is just contention.
EMBEDDING_THREADS = _env_int("EMBEDDING_THREADS", min(8, os.cpu_count() or 4))

# bge-small-en-v1.5 is trained with an instruction prefix on the query side only.
# Set to an empty string to disable and measure the difference.
EMBEDDING_QUERY_PREFIX = os.getenv(
    "EMBEDDING_QUERY_PREFIX",
    "Represent this sentence for searching relevant passages: ",
)

# Recorded on every embedded chunk. Changing the model or backend changes this
# string, which makes stale rows show up as pending and get re-embedded rather
# than leaving the index silently mixed between two incompatible vector spaces.
EMBEDDING_SIGNATURE = f"{EMBEDDING_BACKEND}:{EMBEDDING_MODEL}"

# --------------------------------------------------
# Qdrant
# --------------------------------------------------

QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")

QDRANT_PORT = _env_int("QDRANT_PORT", 6333)

QDRANT_GRPC_PORT = _env_int("QDRANT_GRPC_PORT", 6334)

# gRPC is markedly faster than HTTP for bulk upserts.
QDRANT_PREFER_GRPC = _env_bool("QDRANT_PREFER_GRPC", True)

QDRANT_COLLECTION = _env_str(
    "QDRANT_COLLECTION",
    f"azure_docs_{CHUNK_PROFILE}",
)

# Points per upsert call.
QDRANT_UPSERT_BATCH_SIZE = _env_int("QDRANT_UPSERT_BATCH_SIZE", 512)

# Keep payloads (which include the full chunk text) on disk instead of in RAM.
QDRANT_ON_DISK_PAYLOAD = _env_bool("QDRANT_ON_DISK_PAYLOAD", True)

# HNSW build is deferred while bulk loading, then restored to this threshold.
QDRANT_INDEXING_THRESHOLD = _env_int("QDRANT_INDEXING_THRESHOLD", 20000)

# --------------------------------------------------
# Reranking
# --------------------------------------------------

RERANKER_MODEL = os.getenv(
    "RERANKER_MODEL",
    "cross-encoder/ms-marco-MiniLM-L6-v2",
)

# --------------------------------------------------
# LLM
# --------------------------------------------------

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

LLM_MODEL = _env_str("LLM_MODEL", "gpt-5.4-mini")

# Low but not zero: answers should be near-deterministic for evaluation, and
# temperature is one of the things the LLM evaluation will sweep.
LLM_TEMPERATURE = float(_env_str("LLM_TEMPERATURE", "0.2"))

# gpt-5.x counts reasoning tokens inside completion tokens, so this ceiling has
# to leave room for both the reasoning and the visible answer.
LLM_MAX_OUTPUT_TOKENS = _env_int("LLM_MAX_OUTPUT_TOKENS", 1200)

LLM_TIMEOUT_SECONDS = float(_env_str("LLM_TIMEOUT_SECONDS", "60"))

# Which prompt template to use. See app/llm/prompts.py for the alternatives;
# this is the knob the LLM evaluation sweeps.
LLM_PROMPT = _env_str("LLM_PROMPT", "grounded_mentor")

# Retrieved chunks passed to the model as context.
LLM_CONTEXT_CHUNKS = _env_int("LLM_CONTEXT_CHUNKS", 5)

# --------------------------------------------------
# LLM pricing (USD per 1,000,000 tokens)
# --------------------------------------------------
#
# Used to report cost per answer, which the monitoring dashboard needs. Prices
# are NOT fetchable from the API, so they are hard-coded and go stale.
#
# Only rates that could be verified are listed. An unlisted model reports its
# token counts normally but leaves cost as None rather than inventing a number —
# a wrong cost figure on a dashboard is worse than an absent one. To fill one in,
# check https://openai.com/api/pricing and either add it here or set
# LLM_PRICE_INPUT_PER_1M / LLM_PRICE_OUTPUT_PER_1M in .env.

LLM_PRICING: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4.1-mini": (0.40, 1.60),
}

_price_in = _env_str("LLM_PRICE_INPUT_PER_1M", "")
_price_out = _env_str("LLM_PRICE_OUTPUT_PER_1M", "")

if _price_in and _price_out:
    LLM_PRICING[LLM_MODEL] = (float(_price_in), float(_price_out))

# --------------------------------------------------
# Monitoring
# --------------------------------------------------
#
# Deliberately a SEPARATE database from the index. The index is rebuilt by
# `--fresh` and is namespaced per chunk profile; conversation history and user
# feedback must survive both. Losing months of feedback to a reindex would be
# an unforced error.

MONITORING_DATABASE_PATH = Path(
    _env_str("MONITORING_DATABASE_PATH", str(DATA_DIR / "monitoring.db"))
)

MONITORING_DATABASE_URL = f"sqlite:///{MONITORING_DATABASE_PATH}"

# Log every answered question. Turning this off also disables the dashboard.
MONITORING_ENABLED = _env_bool("MONITORING_ENABLED", True)

# --------------------------------------------------
# Evaluation
# --------------------------------------------------

# Model used by the LLM-as-judge. Kept separate from LLM_MODEL so the judge can
# stay fixed while the answering model is swept, which is the only way the
# comparison between answering models means anything.
JUDGE_MODEL = _env_str("JUDGE_MODEL", LLM_MODEL)

# Judge every live answer as it is produced. This roughly doubles the number of
# API calls per question, so it is a real cost decision, not a free feature.
JUDGE_LIVE_ANSWERS = _env_bool("JUDGE_LIVE_ANSWERS", True)

# Where generated ground truth lands.
GROUND_TRUTH_PATH = Path(
    _env_str("GROUND_TRUTH_PATH", str(DATA_DIR / "ground_truth.csv"))
)

EVAL_RESULTS_DIR = Path(
    _env_str("EVAL_RESULTS_DIR", str(BASE_DIR / "eval_results"))
)
