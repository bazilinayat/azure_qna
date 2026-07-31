# AzureMentor

A retrieval-augmented mentor for learning Microsoft Azure. Ask it a question in
plain English and it answers from the official Azure documentation, with links
back to the pages it used.

> **Status: retrieval pipeline complete, answer generation not yet built.**
> Ingestion, chunking, hybrid search and the pipeline runner work end to end.
> The LLM layer, web interface, evaluation and monitoring are the next steps —
> see [Roadmap](#roadmap).

## The problem

Azure's documentation is enormous and organised by service, not by question.
Someone learning Azure knows what they want to accomplish ("how do I stop a
storage container being publicly readable?") but not which of 145 service areas
holds the answer, nor the product vocabulary needed to search for it. General
chat assistants answer confidently but cite nothing and drift out of date.

AzureMentor indexes the real documentation and answers from it, so every answer
is traceable to a Microsoft Learn page.

## Architecture

```
MicrosoftDocs/azure-docs  (git, sparse checkout)
        |
        v
  clean + parse frontmatter          app/ingest/markdown.py
        |
        v
  header-aware chunking              app/ingest/chunker.py
        |
        v
  SQLite: documents + chunks         app/db/
        |
        +---------------------------+
        |                           |
        v                           v
  FTS5 / BM25 index          bge-small embeddings -> Qdrant
  app/db/fts.py              app/embedding/
        |                           |
        +------------+--------------+
                     v
        Reciprocal Rank Fusion       app/search/rrf.py
                     v
        cross-encoder reranking      app/search/reranker.py
                     v
              top-k chunks
```

## Quick start

Requires Python 3.13+, [uv](https://docs.astral.sh/uv/), and Docker.

```bash
uv sync
```

```bash
cp .env.example .env
```

```bash
docker compose up -d
```

Then build the index. This clones the Azure docs, ingests them, and embeds them:

```bash
uv run python -m app.pipeline --fresh
```

Expect roughly **an hour** on an 8-core CPU for the default 15-service scope
(~3,900 articles, ~27,000 chunks). The run is resumable — if it is interrupted,
rerun the same command and it continues from where it stopped.

Query it:

```bash
uv run python -m app.search.main
```

Or run a single query non-interactively:

```bash
uv run python -m app.search.main "how do I restrict blob access to a vnet"
```

## The pipeline runner

Everything runs through one entry point, `app/pipeline.py`, which executes the
stages in dependency order and logs timing and row counts for each to both the
console and a timestamped file in `logs/`.

| Stage | Does |
|---|---|
| `database` | Creates the schema and the FTS5 virtual table |
| `ingest` | Syncs the docs repo, cleans, chunks, loads SQLite |
| `fts` | Rebuilds the BM25 index from `chunks` |
| `embed` | Embeds chunks and upserts into Qdrant |
| `verify` | Cross-checks counts and runs a live probe query |

```bash
uv run python -m app.pipeline --fresh
```

Useful variations:

```bash
uv run python -m app.pipeline --limit 40
```

```bash
uv run python -m app.pipeline --stages fts,embed
```

```bash
uv run python -m app.pipeline --from embed
```

Exit codes: `0` success, `1` a stage raised, `2` stages ran but `verify` found an
inconsistency.

## Design decisions worth knowing

**Chunking is measured in the embedding model's tokens, not tiktoken.**
`bge-small-en-v1.5` truncates at 512 tokens, and its WordPiece tokenizer emits
~1.27x more tokens than `cl100k_base` on Azure docs — identifiers, hyphenated
resource names and URLs all shred into subwords. Chunking to "512 tiktoken
tokens" therefore produces ~650-token chunks whose tails the encoder silently
discards. Chunks are budgeted at 480 real tokens, and a final pass guarantees
nothing exceeds it.

**Chunks follow document structure and carry their breadcrumb.** Splitting on
markdown headers keeps code blocks, tables and procedures intact, and every chunk
is prefixed with its header path (`Introduction to Azure Blob Storage > Blob
Storage resources > Containers`). That gives the embedding topical grounding and
gives BM25 the service names to match on. Packing runs across sections rather
than restarting at each header — per-section packing fragmented the corpus into
~14 chunks per document averaging a third of the budget.

**`includes/` fragments are skipped.** ~1,480 of the 15,000 markdown files are
reusable partials with no title and no standalone meaning.

**The clone is markdown-only.** `articles/` is ~4 GB, mostly screenshots across
336 `media/` directories, and a plain shallow clone of the repo costs 7.6 GB. A
cone-mode sparse checkout of `articles` would still pull every image, so the
checkout uses a non-cone `*.md` pattern together with `--filter=blob:none`, which
means git never downloads the image blobs at all. Measured result: **252 MB, all
15,024 markdown files, zero non-markdown files.**

**Retrieval is hybrid and fused with RRF.** BM25 catches exact resource names and
CLI flags that embeddings blur; vectors catch paraphrase. RRF combines them
without needing to normalise BM25 against cosine. Each (query variant, retriever)
pair is fused as its own ranking.

**The embedding backend is torch, not fastembed.** fastembed ships the
int8-quantized ONNX build of bge-small, which measured **4x slower** than the
fp32 torch model on identical input on this hardware (2.2 vs 8.3 chunks/s over
128 real chunks). Both backends are implemented; `EMBEDDING_BACKEND` selects one.
They do not share a vector space, so switching re-embeds automatically — the
resume marker records the backend as well as the model.

**Indexing is resumable and self-healing.** `chunks.embedding_model` stores
`backend:model` per row, written only after the Qdrant upsert returns. Changing
model or backend makes stale rows pending again, so the index can never end up
silently split between two incompatible vector spaces.

## Configuration

All settings live in `app/config.py` and are overridable by environment
variable; see [.env.example](.env.example) for the full list with explanations.
The ones that matter most:

| Variable | Default | Notes |
|---|---|---|
| `INGEST_CATEGORIES` | 15 core services | Comma-separated folder names |
| `INGEST_ALL_CATEGORIES` | `false` | `true` indexes all 145, several hours |
| `CHUNK_MAX_TOKENS` | `480` | The retrieval experiment knob, see below |
| `EMBEDDING_BACKEND` | `torch` | or `fastembed` |
| `EMBEDDING_THREADS` | `8` | Throughput plateaus here |

### Chunk size is one knob

Chunk size is the highest-value variable to sweep when evaluating retrieval, so
it is deliberately the only one you need to change. Set it and everything else
follows:

```bash
uv run python -m app.pipeline --fresh --chunk-size 256
```

That derives the overlap (1/8 of the chunk size), the minimum chunk size (1/16),
the database file (`data/azurementor-c256.db`) and the Qdrant collection
(`azure_docs_c256`). Because the storage names carry the chunk profile, **two
chunk sizes never overwrite each other** — build several, then switch between
them instantly:

```bash
uv run python -m app.search.main --chunk-size 256
```

A fixed overlap is the trap this avoids: leaving the 480-token default of 60 in
place while testing 128-token chunks would mean ~50% overlap and a corpus half
made of duplicates. Startup also refuses a chunk size above the embedding
model's limit, since chunks over it are truncated with no error at all.

Do not set `DATABASE_PATH` or `QDRANT_COLLECTION` in `.env` — pinning either one
routes every chunk size to the same store, which silently turns a comparison
between two sizes into a comparison of one size against itself. The pipeline
warns if you have.

## Known gaps

**Corpus coverage.** Microsoft split the old monolithic `azure-docs` repository:
virtual machines, AKS, Key Vault, Cosmos DB, Monitor, Machine Learning and Entra
ID now live in separate repositories and are **not** in this index. `SOURCE_REPOS`
in `app/config.py` is a list specifically so they can be added.

**Ingest is not incremental.** Re-running `ingest` clears and reloads, which
reassigns chunk ids and forces a full re-embed. Adding a category is therefore a
full rebuild. Content-hash-based upsert would fix this.

## Roadmap

Ordered by dependency — the LLM layer gates everything below it.

- [ ] **LLM layer** (`app/llm/`) — answer generation over retrieved chunks
- [ ] **Agentic RAG** — let the agent decide whether to search, refine, or answer
- [ ] **Retrieval evaluation** — ground-truth set, hit rate and MRR, comparing
      keyword vs vector vs hybrid vs hybrid+rerank
- [ ] **LLM evaluation** — LLM-as-judge over multiple prompt variants
- [ ] **Streamlit interface** with thumbs-up/down feedback capture
- [ ] **Monitoring** — persist conversations, feedback, latency, token cost;
      Grafana dashboard
- [ ] **Full containerization** — application in docker-compose, not just
      dependencies
- [ ] **Cloud deployment**

## Project layout

```
app/
  config.py            all configuration, env-overridable
  logging_setup.py     console + file logging
  pipeline.py          the single entry point
  db/
    connection.py      engine, session factory, SQLite pragmas
    schema.py          documents + chunks tables
    fts.py             FTS5 index management and query escaping
    db_init.py         schema creation / reset
  ingest/
    ingest.py          repo sync and load orchestration
    markdown.py        frontmatter parsing, Learn directive cleaning
    chunker.py         header-aware, token-budgeted chunking
    tokenizer.py       token counting in the embedding model's space
  embedding/
    embedder.py        torch and fastembed backends
    qdrant_client.py   collection lifecycle, bulk load tuning, search
    index.py           resumable embed-and-upsert
  search/
    hybrid_search.py   orchestration
    keyword_search.py  BM25 over FTS5
    vector_search.py   dense retrieval
    rrf.py             reciprocal rank fusion
    reranker.py        cross-encoder
    query_expander.py  Azure acronym expansion
    main.py            interactive CLI
```

## Acknowledgements

Built for the [DataTalks.Club LLM Zoomcamp](https://github.com/DataTalksClub/llm-zoomcamp).
Documentation content is from [MicrosoftDocs/azure-docs](https://github.com/MicrosoftDocs/azure-docs),
licensed CC-BY-4.0.
