# Grafana over the monitoring database

Grafana has no built-in SQLite datasource, so this uses the community plugin
[`frser-sqlite-datasource`](https://github.com/fr-ser/grafana-sqlite-datasource)
(v4.0.6 at time of writing). Both the plugin and the datasource are provisioned
automatically — `docker compose up -d grafana` is all that is needed.

Grafana is at <http://localhost:3000>. Anonymous visitors get read-only access;
log in as `admin` / `admin` (override with `GRAFANA_ADMIN_PASSWORD` in `.env`)
to edit.

## How the wiring works

| Piece | Where |
|---|---|
| Plugin install | `GF_PLUGINS_PREINSTALL_SYNC` in `compose.yaml` |
| Datasource | `grafana/provisioning/datasources/sqlite.yml` |
| Database file | `./data/monitoring.db`, mounted at `/var/lib/azurementor` |

Three things that are easy to get wrong:

**Use `GF_PLUGINS_PREINSTALL_SYNC`, not `GF_PLUGINS_PREINSTALL`.** The non-sync
variant installs in the background, so datasource provisioning can run before the
plugin exists and fail with "datasource type not found". (`GF_INSTALL_PLUGINS`,
which most tutorials still use, is deprecated in Grafana 13.)

**Mount the data directory read-write.** SQLite in WAL mode writes a `-shm` index
file even for read-only queries, so a `:ro` mount makes every query fail.

**Monitoring lives in its own database.** `data/monitoring.db` is separate from
the search index (`data/azurementor-c480.db`) because the index is dropped and
rebuilt by `app.pipeline --fresh`. Conversations and feedback survive that.

## Schema

`conversations` — one row per answered question:

| Column | Notes |
|---|---|
| `id`, `session_id` | UUIDs; session groups one browser tab |
| `question`, `answer` | |
| `model`, `prompt_name`, `chunk_profile`, `embedding_model` | what produced it |
| `retrieval_seconds`, `generation_seconds`, `total_seconds` | |
| `prompt_tokens`, `completion_tokens`, `reasoning_tokens`, `total_tokens` | |
| `cost_usd` | NULL when the model's price is unknown |
| `num_sources_retrieved`, `num_sources_cited` | |
| `sources` | JSON array of cited sources |
| `relevance` | `RELEVANT` / `PARTLY_RELEVANT` / `NON_RELEVANT` from the judge |
| `relevance_explanation`, `judge_model`, `judge_tokens`, `judge_cost_usd` | |
| `created_at` | datetime |
| `created_at_unix` | epoch seconds — **use this for time series** |

`feedback` — thumbs up/down, `value` is `+1` or `-1`, joined by `conversation_id`.

## Time series panels

The plugin needs an integer epoch column named `time`, in **seconds**. Set the
panel's query format to *Time series* and alias `created_at_unix`:

```sql
SELECT
  created_at_unix AS time,
  total_seconds   AS "response time (s)"
FROM conversations
WHERE $__unixEpochFilter(created_at_unix)
ORDER BY created_at_unix
```

`$__unixEpochFilter(...)` is what makes the panel obey the dashboard's time
picker. Without it the panel ignores the selected range.

## Queries to start from

**Questions per hour**

```sql
SELECT
  CAST(created_at_unix / 3600 AS INTEGER) * 3600 AS time,
  COUNT(*) AS questions
FROM conversations
WHERE $__unixEpochFilter(created_at_unix)
GROUP BY 1
ORDER BY 1
```

**Answer relevance over time** (the judge's verdict, stacked)

```sql
SELECT
  CAST(created_at_unix / 3600 AS INTEGER) * 3600 AS time,
  SUM(CASE WHEN relevance = 'RELEVANT'        THEN 1 ELSE 0 END) AS relevant,
  SUM(CASE WHEN relevance = 'PARTLY_RELEVANT' THEN 1 ELSE 0 END) AS partly,
  SUM(CASE WHEN relevance = 'NON_RELEVANT'    THEN 1 ELSE 0 END) AS non_relevant
FROM conversations
WHERE $__unixEpochFilter(created_at_unix)
GROUP BY 1
ORDER BY 1
```

**Relevance breakdown** (pie chart — format *Table*)

```sql
SELECT COALESCE(relevance, 'UNJUDGED') AS relevance, COUNT(*) AS count
FROM conversations
GROUP BY 1
```

**Latency split, retrieval vs generation**

```sql
SELECT
  created_at_unix AS time,
  retrieval_seconds  AS retrieval,
  generation_seconds AS generation
FROM conversations
WHERE $__unixEpochFilter(created_at_unix)
ORDER BY 1
```

**Cost per day** (NULL-safe: unpriced models contribute 0, not NULL)

```sql
SELECT
  CAST(created_at_unix / 86400 AS INTEGER) * 86400 AS time,
  SUM(COALESCE(cost_usd, 0)) AS "cost (USD)"
FROM conversations
WHERE $__unixEpochFilter(created_at_unix)
GROUP BY 1
ORDER BY 1
```

**Token usage per day**

```sql
SELECT
  CAST(created_at_unix / 86400 AS INTEGER) * 86400 AS time,
  SUM(prompt_tokens)     AS prompt,
  SUM(completion_tokens) AS completion
FROM conversations
WHERE $__unixEpochFilter(created_at_unix)
GROUP BY 1
ORDER BY 1
```

**User feedback score** (stat panel — format *Table*)

```sql
SELECT
  SUM(CASE WHEN value =  1 THEN 1 ELSE 0 END) AS thumbs_up,
  SUM(CASE WHEN value = -1 THEN 1 ELSE 0 END) AS thumbs_down,
  SUM(value) AS net
FROM feedback
```

**Where the judge and the user disagree** — the most useful table on the
dashboard. These are answers the judge liked and the user did not, which is where
prompt problems actually surface.

```sql
SELECT
  c.created_at,
  c.question,
  c.relevance,
  f.value AS user_vote,
  c.relevance_explanation
FROM conversations c
JOIN feedback f ON f.conversation_id = c.id
WHERE (f.value = -1 AND c.relevance = 'RELEVANT')
   OR (f.value =  1 AND c.relevance = 'NON_RELEVANT')
ORDER BY c.created_at_unix DESC
```

**Questions the system could not answer** — the content gap list, i.e. what to
index next.

```sql
SELECT created_at, question, num_sources_retrieved
FROM conversations
WHERE num_sources_cited = 0
ORDER BY created_at_unix DESC
```

**Answer quality by prompt template** — the live counterpart to the offline
prompt sweep.

```sql
SELECT
  prompt_name,
  COUNT(*) AS answers,
  ROUND(AVG(CASE relevance
    WHEN 'RELEVANT' THEN 1.0
    WHEN 'PARTLY_RELEVANT' THEN 0.5
    ELSE 0.0 END), 3) AS mean_score,
  ROUND(AVG(total_seconds), 2) AS avg_seconds
FROM conversations
GROUP BY prompt_name
ORDER BY mean_score DESC
```

## If a panel shows no data

1. Check the dashboard time range — rows are timestamped when asked, so "Last 15
   minutes" hides everything from yesterday.
2. Confirm rows exist: `uv run python -m app.eval.main live`
3. Test the datasource in *Connections → Data sources → AzureMentor → Save & test*.
4. Check Grafana can see the file: `docker compose exec grafana ls -la /var/lib/azurementor`
