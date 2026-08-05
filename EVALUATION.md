# Evaluation

Two things are measured separately, because they fail for different reasons:
**retrieval** (did we find the right documentation?) and **answers** (given what
we found, was the reply any good?).

---

## Ground truth

450 questions in [`data/ground_truth.csv`](data/ground_truth.csv), committed so
results are reproducible.

They are synthetic. An LLM reads an indexed document and writes questions that
document answers, which makes the source document the correct answer by
construction — that is what makes hit rate and MRR computable without hand
labelling hundreds of pairs.

```bash
uv run python -m app.eval.main generate --sample 150 --per-document 3
```

**The bias to keep in mind:** questions generated from a document tend to reuse
its vocabulary, which flatters keyword search. The generation prompt pushes
against this — it asks for a learner's phrasing, forbids copying sentences, and
requires each question to name its service explicitly so it stands alone — but
the bias cannot be fully removed. Treat absolute numbers as soft and the
*comparisons between configurations* as sound.

---

## Retrieval evaluation

```bash
uv run python -m app.eval.main retrieval
```

No API calls, so this is free to re-run after any retrieval change. Six
configurations, same 450 questions, top-5 results.

**Metrics.** *Hit rate* is the fraction of questions whose correct document
appears anywhere in the top 5 — "did the answer reach the LLM at all?" *MRR* is
the mean of 1/rank, which rewards ranking the right document first rather than
fifth. Both are computed over **documents**, not chunks: several chunks of the
same article are all correct.

### Results

| Configuration | Hit rate | MRR | Hit@1 | Hit@3 | s/query |
|---|---|---|---|---|---|
| keyword only (BM25) | 0.900 | 0.765 | 0.673 | 0.847 | 0.25 |
| vector only | 0.876 | 0.767 | 0.696 | 0.833 | **0.07** |
| **hybrid (RRF)** | 0.933 | **0.835** | **0.771** | 0.896 | 0.33 |
| hybrid + expansion | 0.927 | 0.828 | 0.764 | 0.891 | 0.37 |
| hybrid + rerank | 0.940 | 0.825 | 0.749 | 0.898 | 1.91 |
| hybrid + expansion + rerank | **0.942** | 0.825 | 0.749 | 0.896 | 1.95 |

*n = 450. Approximate standard error on hit rate is √(p(1−p)/n) ≈ **1.2
percentage points**, which is the bar any difference has to clear to mean
anything.*

### What the numbers say

**Hybrid search is a real win.** Fusing BM25 and vectors lifts MRR from 0.765
(keyword) and 0.767 (vector) to **0.835** — around 7 percentage points, roughly
six times the standard error. Hit@1 rises from 0.673/0.696 to 0.771. This is the
clearest result in the table and it is why both retrievers are on by default.

It also matches what each retriever is good at. BM25 nails exact resource names
and CLI flags (`Standard_LRS`, `--allow-blob-public-access`) that embeddings
blur; vectors handle paraphrase, which is most of how people actually ask. RRF
combines the two rankings without having to normalise BM25 scores against cosine
similarity.

**Reranking did not earn its cost.** The cross-encoder bought +0.7pp hit rate —
*inside* the 1.2pp standard error, so not a real difference — while **lowering**
MRR by 1.0pp and hit@1 by 2.2pp, at **5.7× the latency** (1.91s vs 0.33s).

The hit@1 drop is the most interesting part: the reranker demotes the correct
document out of first place about 2% of the time. `ms-marco-MiniLM-L6-v2` is
trained on web search passages, and Azure documentation chunks — dense with
commands, tables and resource identifiers — are some distance from that
distribution.

**Query expansion was slightly negative** on every metric (−0.7pp hit, −0.6pp
MRR). Acronym substitution helps when someone types `vm` or `rbac`, but that is
not most questions, and the extra query variants dilute the fusion for everything
else.

### What is used

**Hybrid (RRF), with expansion and reranking off.** Best MRR, best hit@1, hit
rate statistically indistinguishable from the best, and 5.7× faster than the
reranked configurations.

Both are still implemented, tested and evaluated — they are off because the
measurement said so, not because they are missing. Re-enable either in
`app/search/search_config.py`, or invert from the CLI:

```bash
uv run python -m app.search.main --no-expand "how do I resize a vm"
```

### Honest caveats

Retrieval metrics are a proxy. They measure whether the right *document* was
retrieved, not whether the answer was good — it is possible reranking improves
answer quality in ways MRR cannot see, by putting the most useful chunk where the
LLM attends most. The answer evaluation below is the check on that, and running
it with reranking on is the obvious follow-up experiment.

Synthetic ground truth also favours keyword matching, so the true gap between
`keyword only` and the hybrid configurations is likely **wider** than the table
shows, not narrower.

---

## Answer evaluation

```bash
uv run python -m app.eval.main answers --sample 30
```

Three prompt templates answer the same questions, and an LLM-as-judge scores each
answer `RELEVANT` / `PARTLY_RELEVANT` / `NON_RELEVANT`.

| Template | Idea |
|---|---|
| `grounded_mentor` | Teaching tone, cites sources, explains why, refuses when uncovered |
| `concise` | Direct and brief. Tests whether the teaching preamble earns its tokens |
| `strict_extractive` | Quote-only, no interpretation. The control |

Alongside relevance the report gives a **grounding rate** — how often the expected
document was actually retrieved. That separates the two failure modes: high
grounding with low relevance is a prompt problem; low grounding is a retrieval
problem and no prompt will fix it.

The judge runs at temperature 0, because a judge that disagrees with itself
between runs cannot be used to compare two systems. It is deliberately told to
grade *relevance to the question*, not factual correctness — checking facts would
require it to know Azure, which reintroduces exactly the hallucination risk the
project is designed to avoid.

> **Not yet run.** This costs `prompts × questions × 2` API calls. Run it and
> paste the table here.

---

## Live evaluation

The **same judge** scores every answer served by the app, so offline and
production numbers are directly comparable — a score measured one way offline and
another way live cannot be compared, which defeats the point of measuring either.

```bash
uv run python -m app.eval.main live
```

Live relevance, user thumbs up/down, and the disagreements between them are all
queryable from Grafana; see [grafana/README.md](grafana/README.md). The
judge-versus-user disagreement table is the most useful one on the dashboard —
answers the judge liked but users downvoted are where prompt problems surface.

Judging live answers doubles API calls per question. Turn it off with
`JUDGE_LIVE_ANSWERS=false`.

---

## Reproducing

```bash
docker compose up -d qdrant
```

```bash
uv run python -m app.eval.main retrieval
```

Roughly 35 minutes for all six configurations; the reranking ones dominate.
Results are written to `eval_results/` after **every** configuration, so a crash
late in the run does not discard what already succeeded.

If a configuration runs out of memory — the reranking ones hold the cross-encoder,
the embedding model and Qdrant results at once — stop the app container and rerun
just that one:

```bash
docker compose --profile app down
```

```bash
uv run python -m app.eval.main retrieval --configs "hybrid + rerank"
```
