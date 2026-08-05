# Grafana over the monitoring database

Reference for the datasource wiring, the schema, and a cookbook of queries.

For **building panels** — including the seven from the course lesson translated
panel by panel — see **[DASHBOARD.md](DASHBOARD.md)**. A dashboard with 13 panels
is already provisioned at <http://localhost:3000/d/azurementor-monitoring>.

Every query on this page has been executed against the live database.

## Setup

Grafana has no built-in SQLite datasource, so this uses the community plugin
[`frser-sqlite-datasource`](https://github.com/fr-ser/grafana-sqlite-datasource)
(v4.0.6 at time of writing). Plugin, datasource and dashboard are all provisioned
automatically — `docker compose up -d grafana` is all that is needed.

Grafana is at <http://localhost:3000>. Anonymous visitors get read-only access;
**log in as `admin` / `admin`** to create or edit anything (override with
`GRAFANA_ADMIN_PASSWORD` in `.env`). Viewers cannot create dashboards, which is
the usual reason the "New" button appears to be missing.

| Piece | Where |
|---|---|
| Plugin install | `GF_PLUGINS_PREINSTALL_SYNC` in `compose.yaml` |
| Datasource | `grafana/provisioning/datasources/sqlite.yml` |
| Dashboard | `grafana/provisioning/dashboards/azurementor.json` |
| Database file | `./data/monitoring.db`, mounted at `/var/lib/azurementor` |

Three things that are easy to get wrong:

**Use `GF_PLUGINS_PREINSTALL_SYNC`, not `GF_PLUGINS_PREINSTALL`.** The non-sync
variant installs in the background, so datasource provisioning can run before the
plugin exists and fail with "datasource type not found". (`GF_INSTALL_PLUGINS`,
which most tutorials still use, is deprecated in Grafana 13.)

**Keep the monitoring database out of WAL mode.** WAL needs an `mmap`-backed
`-shm` file, which does not work on Docker Desktop's Windows bind mounts: Grafana
cannot open the database at all and every panel fails with `unable to open
database file (14)`. It is also unsafe — without working shared memory, two
processes disagreeing about journal mode can lose committed rows.
`MONITORING_JOURNAL_MODE` defaults to `DELETE` for exactly this reason. Mount
`./data` read-write regardless, so SQLite can create its journal.

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
| `created_at` | datetime text, for tables |
| `created_at_unix` | epoch seconds — **use this for time series and filtering** |

`feedback` — thumbs up/down, `value` is `+1` or `-1`, joined by `conversation_id`.

## Writing queries for this plugin

Two rules, both different from the PostgreSQL examples most tutorials use.

**Time filtering uses Grafana's global variables, not a datasource macro.** This
plugin implements exactly one macro — `$__unixEpochGroupSeconds` — and none of
`$__timeFilter`, `$__timeFrom`, `$__timeTo`, `$__timeGroup` or `$__unixEpochFilter`
exist. Use `$__from` and `$__to`, which Grafana interpolates itself so they work
with any datasource. They are in **milliseconds**, and our column is in seconds:

```
WHERE created_at_unix BETWEEN $__from/1000 AND $__to/1000
```

**Time-series panels need the time column declared.** In the query editor set
**Query Type** to `Time series` *and* put `time` in **Time formatted columns**.
That second setting is what converts epoch seconds into what Grafana plots;
without it the panel renders nothing. It is the most common cause of an empty
time-series panel.

Bucketing uses the one supported macro:

```
$__unixEpochGroupSeconds(created_at_unix, 3600)   -- hourly
$__unixEpochGroupSeconds(created_at_unix, 86400)  -- daily
```

## Query cookbook

Panels already on the provisioned dashboard are marked ✓. The rest are extras
worth adding.

### Questions per hour ✓

```sql
SELECT $__unixEpochGroupSeconds(created_at_unix, 3600) AS time,
       COUNT(*) AS questions
FROM conversations
WHERE created_at_unix BETWEEN $__from/1000 AND $__to/1000
GROUP BY 1
ORDER BY 1
```

### Answer relevance over time

The judge's verdict, stacked. Set the panel to stacked bars.

```sql
SELECT $__unixEpochGroupSeconds(created_at_unix, 3600) AS time,
       SUM(CASE WHEN relevance = 'RELEVANT'        THEN 1 ELSE 0 END) AS relevant,
       SUM(CASE WHEN relevance = 'PARTLY_RELEVANT' THEN 1 ELSE 0 END) AS partly,
       SUM(CASE WHEN relevance = 'NON_RELEVANT'    THEN 1 ELSE 0 END) AS non_relevant
FROM conversations
WHERE created_at_unix BETWEEN $__from/1000 AND $__to/1000
GROUP BY 1
ORDER BY 1
```

### Relevance breakdown ✓

Pie chart, Query Type `Table`, and set Value options → Show → **All values**.

```sql
SELECT COALESCE(relevance, 'UNJUDGED') AS relevance, COUNT(*) AS count
FROM conversations
WHERE created_at_unix BETWEEN $__from/1000 AND $__to/1000
GROUP BY 1
```

### Latency split, retrieval vs generation ✓

```sql
SELECT $__unixEpochGroupSeconds(created_at_unix, 3600) AS time,
       ROUND(AVG(retrieval_seconds), 3)  AS retrieval,
       ROUND(AVG(generation_seconds), 3) AS generation
FROM conversations
WHERE created_at_unix BETWEEN $__from/1000 AND $__to/1000
GROUP BY 1
ORDER BY 1
```

### Cost per day ✓

`COALESCE` matters — `cost_usd` is NULL for models whose price is unknown, and
`SUM` over NULLs returns NULL rather than 0.

```sql
SELECT $__unixEpochGroupSeconds(created_at_unix, 86400) AS time,
       ROUND(SUM(COALESCE(cost_usd, 0)), 6) AS cost
FROM conversations
WHERE created_at_unix BETWEEN $__from/1000 AND $__to/1000
GROUP BY 1
ORDER BY 1
```

### Token usage per day

```sql
SELECT $__unixEpochGroupSeconds(created_at_unix, 86400) AS time,
       SUM(prompt_tokens)     AS prompt,
       SUM(completion_tokens) AS completion
FROM conversations
WHERE created_at_unix BETWEEN $__from/1000 AND $__to/1000
GROUP BY 1
ORDER BY 1
```

### User feedback score ✓

Stat panel, Query Type `Table`.

```sql
SELECT SUM(CASE WHEN value =  1 THEN 1 ELSE 0 END) AS thumbs_up,
       SUM(CASE WHEN value = -1 THEN 1 ELSE 0 END) AS thumbs_down,
       SUM(value) AS net
FROM feedback
WHERE created_at_unix BETWEEN $__from/1000 AND $__to/1000
```

### Where the judge and the user disagree ✓

The most useful table on the dashboard. These are answers the judge liked and the
user did not, and the reverse — where prompt problems actually surface, and what
the aggregate percentages hide.

```sql
SELECT c.created_at,
       c.question,
       c.relevance,
       CASE WHEN f.value = 1 THEN 'thumbs up' ELSE 'thumbs down' END AS user_vote,
       c.relevance_explanation
FROM conversations c
JOIN feedback f ON f.conversation_id = c.id
WHERE ((f.value = -1 AND c.relevance = 'RELEVANT')
    OR (f.value =  1 AND c.relevance = 'NON_RELEVANT'))
  AND c.created_at_unix BETWEEN $__from/1000 AND $__to/1000
ORDER BY c.created_at_unix DESC
```

### Questions the system could not answer

The content gap list — what to index next. `num_sources_cited = 0` means the
answer cited nothing, which usually means the documentation did not cover it.

```sql
SELECT created_at, question, num_sources_retrieved
FROM conversations
WHERE num_sources_cited = 0
  AND created_at_unix BETWEEN $__from/1000 AND $__to/1000
ORDER BY created_at_unix DESC
```

### Answer quality by prompt template

The live counterpart to the offline prompt sweep in
[EVALUATION.md](../EVALUATION.md).

```sql
SELECT prompt_name,
       COUNT(*) AS answers,
       ROUND(AVG(CASE relevance
         WHEN 'RELEVANT' THEN 1.0
         WHEN 'PARTLY_RELEVANT' THEN 0.5
         ELSE 0.0 END), 3) AS mean_score,
       ROUND(AVG(total_seconds), 2) AS avg_seconds
FROM conversations
WHERE created_at_unix BETWEEN $__from/1000 AND $__to/1000
GROUP BY prompt_name
ORDER BY mean_score DESC
```

### Busiest sessions

```sql
SELECT session_id,
       COUNT(*) AS questions,
       ROUND(AVG(total_seconds), 2) AS avg_seconds,
       SUM(total_tokens) AS tokens
FROM conversations
WHERE created_at_unix BETWEEN $__from/1000 AND $__to/1000
GROUP BY session_id
ORDER BY questions DESC
LIMIT 20
```

## Generating data to look at

```bash
uv run python -m app.monitoring.seed --synthetic --questions 180 --days 7
```

Free and instant. See [DASHBOARD.md](DASHBOARD.md) for real-traffic mode and how
to remove synthetic rows again.

## If a panel shows no data

1. **Time range.** Rows are stamped when the question was asked; the dashboard
   defaults to *Last 6 hours*.
2. **Time formatted columns is empty.** Time-series panels need `time` there.
3. **A macro that does not exist.** Only `$__unixEpochGroupSeconds` is supported.
4. **Pie shows one number.** Value options → Show → **All values**.
5. **Confirm rows exist:** `uv run python -m app.eval.main live`
6. **Test the datasource:** *Connections → Data sources → AzureMentor → Save & test*.
7. **`unable to open database file (14)`** — the database is in WAL mode. See the
   setup notes above; check with:

   ```bash
   uv run python -c "import sqlite3; print(sqlite3.connect('data/monitoring.db').execute('PRAGMA journal_mode').fetchone())"
   ```
