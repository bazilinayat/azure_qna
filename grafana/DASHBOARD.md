# Building the dashboard

Follows [lesson 12 of module 5](https://github.com/DataTalksClub/llm-zoomcamp/blob/main/05-monitoring/lessons/12-grafana.md),
adapted from the lesson's PostgreSQL to our SQLite.

Two things differ enough to break a copy-paste, and both are covered below: the
**macros** and the **schema**.

---

## First: why "New dashboard" is missing

Nothing is broken. `compose.yaml` sets:

```yaml
GF_AUTH_ANONYMOUS_ENABLED: "true"
GF_AUTH_ANONYMOUS_ORG_ROLE: Viewer
```

So visiting <http://localhost:3000> logs you in automatically as an anonymous
**Viewer**, and Grafana hides every editing control from viewers. Confirmed
against the API — creating a dashboard anonymously returns `403`, and the same
call as admin returns `200`.

**Fix: sign in.** Go to <http://localhost:3000/login> and use `admin` / `admin`
(or whatever `GRAFANA_ADMIN_PASSWORD` is set to in `.env`). The "New" button and
panel edit menus appear immediately.

The anonymous Viewer role is deliberate — it means you can show the dashboard to
someone without handing over the admin password. If you would rather skip the
login step, change the role in `compose.yaml` and restart:

```yaml
GF_AUTH_ANONYMOUS_ORG_ROLE: Admin
```

---

## The dashboard already exists

A dashboard with **13 panels** covering all seven from the lesson is provisioned
automatically from
[`provisioning/dashboards/azurementor.json`](provisioning/dashboards/azurementor.json).

```bash
docker compose up -d grafana
```

Open <http://localhost:3000/d/azurementor-monitoring>. Every panel query has been
executed against the live database and returns data.

Use it as a starting point: sign in, edit any panel, and read the SQL in the
query editor. If you would rather build your own from scratch, the rest of this
document is the panel-by-panel translation.

> Provisioned dashboards are re-applied on container restart, which overwrites
> UI edits. To keep a change, use **Dashboard settings → JSON Model**, copy it,
> and paste it back into the JSON file.

---

## What has to change from the lesson

### 1. Macros — the big one

The lesson uses PostgreSQL macros. The SQLite plugin re-implements macros from
scratch and supports **exactly one**, per its own README:

```
$__unixEpochGroupSeconds(unixEpochColumnName, intervalInSeconds)
```

That is the whole list. `$__timeFilter`, `$__timeFrom`, `$__timeTo`,
`$__timeGroup` and `$__unixEpochFilter` **do not exist here** and produce errors
like `missing named argument` or `unrecognized token`.

For time filtering, use Grafana's global built-in variables instead. `$__from`
and `$__to` are interpolated by Grafana itself, not the plugin, so they work with
any datasource. They are in **milliseconds**, and our column is in seconds:

| Lesson (PostgreSQL) | Here (SQLite) |
|---|---|
| `WHERE timestamp BETWEEN $__timeFrom() AND $__timeTo()` | `WHERE created_at_unix BETWEEN $__from/1000 AND $__to/1000` |
| `$__timeGroup(timestamp, $__interval)` | `$__unixEpochGroupSeconds(created_at_unix, 3600)` |

Verified: with a 5-year range the filter returns 5 rows, with a range in the
distant past it returns 0. It genuinely filters.

### 2. Schema

Our tables carry more per answer, and split judge verdicts from user votes
instead of putting both in one `feedback` table with a `source` column.

| Lesson column | Ours | Note |
|---|---|---|
| `conversations.timestamp` | `conversations.created_at_unix` | epoch **seconds**; `created_at` is the readable text version |
| `conversations.response_time` | `conversations.total_seconds` | also `retrieval_seconds`, `generation_seconds` |
| `conversations.cost` | `conversations.cost_usd` | **nullable** when the model's price is unknown |
| `feedback.relevance` where `source='judge'` | `conversations.relevance` | on the conversation row itself |
| `feedback.score` where `source='user'` | `feedback.value` | `+1` / `-1`; no `source` column, every row is a user vote |

Full schema in [README.md](README.md).

### 3. The time column setting

For any time-series panel the plugin needs to be told which column is time.
In the query editor set:

- **Query Type**: `Time series`
- **Time formatted columns**: `time`

Without that second setting the panel gets a bare integer and renders nothing
useful. It is the single most common reason a time-series panel comes up empty.

---

## The seven lesson panels, translated

Each is a new panel: **New → New dashboard → Add visualisation → AzureMentor**.
Paste the SQL into the query editor and set the type and settings listed.

### 1. Response time — *Time series*

Query Type `Time series`, Time formatted columns `time`.

```sql
SELECT $__unixEpochGroupSeconds(created_at_unix, 3600) AS time,
       ROUND(AVG(retrieval_seconds), 3)  AS retrieval,
       ROUND(AVG(generation_seconds), 3) AS generation
FROM conversations
WHERE created_at_unix BETWEEN $__from/1000 AND $__to/1000
GROUP BY 1
ORDER BY 1
```

Split into two series rather than one total, so a slowdown can be attributed to
search or to the LLM. Set unit to **seconds (s)** under Standard options.

### 2. Token usage — *Time series*

```sql
SELECT $__unixEpochGroupSeconds(created_at_unix, 3600) AS time,
       ROUND(AVG(prompt_tokens))     AS prompt,
       ROUND(AVG(completion_tokens)) AS completion
FROM conversations
WHERE created_at_unix BETWEEN $__from/1000 AND $__to/1000
GROUP BY 1
ORDER BY 1
```

Prompt tokens dominate; they are driven by `LLM_CONTEXT_CHUNKS`.

### 3. Cost — *Time series*, bars

```sql
SELECT $__unixEpochGroupSeconds(created_at_unix, 86400) AS time,
       ROUND(SUM(COALESCE(cost_usd, 0)), 6) AS cost
FROM conversations
WHERE created_at_unix BETWEEN $__from/1000 AND $__to/1000
GROUP BY 1
ORDER BY 1
```

`COALESCE` matters: `cost_usd` is NULL for models whose price is not in
`LLM_PRICING`, and `SUM` over NULLs returns NULL rather than 0. **This panel
reads zero until you set `LLM_PRICE_INPUT_PER_1M` and `LLM_PRICE_OUTPUT_PER_1M`
in `.env`.** Set unit to **currency USD**.

The lesson filters `AND cost > 0`; that is dropped here so days with traffic but
unknown pricing still show up as a bar rather than vanishing.

### 4. Model usage — *Pie chart*

```sql
SELECT model, COUNT(*) AS count
FROM conversations
WHERE created_at_unix BETWEEN $__from/1000 AND $__to/1000
GROUP BY model
```

Query Type `Table`. Under the panel options set **Value options → Show: All
values**, otherwise the pie shows a single reduced number.

### 5. Relevance distribution — *Pie chart*

```sql
SELECT COALESCE(relevance, 'UNJUDGED') AS relevance, COUNT(*) AS count
FROM conversations
WHERE created_at_unix BETWEEN $__from/1000 AND $__to/1000
GROUP BY 1
```

Note the difference from the lesson: our judge verdict lives on the conversation
row, so there is no `feedback` join and no `source = 'judge'` filter.

`COALESCE` surfaces unjudged answers instead of silently dropping them — they
appear when `JUDGE_LIVE_ANSWERS=false`, or when a judge call failed.

Worth adding colour overrides: green for `RELEVANT`, orange for
`PARTLY_RELEVANT`, red for `NON_RELEVANT`.

### 6. User feedback — *Stat*

```sql
SELECT SUM(CASE WHEN value =  1 THEN 1 ELSE 0 END) AS "thumbs up",
       SUM(CASE WHEN value = -1 THEN 1 ELSE 0 END) AS "thumbs down"
FROM feedback
WHERE created_at_unix BETWEEN $__from/1000 AND $__to/1000
```

Two values in one row, so a Stat panel shows both side by side. Set **Color mode:
Background** and override thumbs up to green, thumbs down to red.

### 7. Recent conversations — *Table*

```sql
SELECT created_at,
       question,
       COALESCE(relevance, 'UNJUDGED') AS relevance,
       ROUND(total_seconds, 2) AS seconds,
       total_tokens AS tokens,
       num_sources_cited AS cited
FROM conversations
WHERE created_at_unix BETWEEN $__from/1000 AND $__to/1000
ORDER BY created_at_unix DESC
LIMIT 20
```

Uses `created_at` (readable text) rather than `created_at_unix`, since a table
does not need an epoch. Do **not** alias it to `time` or add it to Time formatted
columns — the plugin would try to convert a string.

`num_sources_cited` is a useful extra: a `0` means the answer cited nothing,
which usually means the documentation did not cover the question.

---

## Beyond the lesson

Three more panels are in the provisioned dashboard because they answer questions
the lesson's set cannot.

**Questions per hour** — traffic volume, which the lesson only shows indirectly.

```sql
SELECT $__unixEpochGroupSeconds(created_at_unix, 3600) AS time,
       COUNT(*) AS questions
FROM conversations
WHERE created_at_unix BETWEEN $__from/1000 AND $__to/1000
GROUP BY 1
ORDER BY 1
```

**Judged relevant %** — a single Stat, the fastest read on whether quality moved.

```sql
SELECT ROUND(100.0 * SUM(CASE WHEN relevance = 'RELEVANT' THEN 1 ELSE 0 END)
             / COUNT(*), 1) AS percent
FROM conversations
WHERE relevance IS NOT NULL
  AND created_at_unix BETWEEN $__from/1000 AND $__to/1000
```

**Judge and user disagree** — the most useful table on the dashboard. Answers the
judge rated RELEVANT that a user downvoted are where prompt problems actually
surface; the aggregate percentages hide them.

```sql
SELECT c.created_at, c.question, c.relevance,
       CASE WHEN f.value = 1 THEN 'thumbs up' ELSE 'thumbs down' END AS user_vote,
       c.relevance_explanation
FROM conversations c
JOIN feedback f ON f.conversation_id = c.id
WHERE ((f.value = -1 AND c.relevance = 'RELEVANT')
    OR (f.value =  1 AND c.relevance = 'NON_RELEVANT'))
  AND c.created_at_unix BETWEEN $__from/1000 AND $__to/1000
ORDER BY c.created_at_unix DESC
```

This one is empty until there are disagreements, which is the correct result, not
a broken panel.

---

## Generating data to look at

An empty dashboard is hard to build against. There is a seeder with two modes.

**Free, instant, no API calls.** Writes plausible rows spread over the past week
so the time-series panels have a shape:

```bash
uv run python -m app.monitoring.seed --synthetic --questions 180 --days 7
```

**Real traffic.** Asks genuine questions through the real pipeline, so latency,
tokens and judge verdicts are all true. Costs roughly two API calls per question:

```bash
uv run python -m app.monitoring.seed --questions 25
```

Synthetic rows are tagged with a `synthetic-` session id and can be removed
without touching real traffic:

```bash
uv run python -m app.monitoring.seed --clear-synthetic
```

Use `--synthetic` to build and lay out panels; use real mode before taking any
screenshot you will present as live usage.

You can also just use the app — open <http://localhost:8501>, ask questions, and
click thumbs up or down. The feedback panels need votes to show anything.

Confirm rows are landing either way:

```bash
uv run python -m app.eval.main live
```

---

## When a panel shows nothing

Work down this list; it is roughly in order of likelihood.

1. **Time range.** Rows are stamped when the question was asked. The dashboard
   defaults to *Last 6 hours* — widen it to *Last 7 days* if you were testing
   yesterday.
2. **Time formatted columns is empty.** Time-series panels need `time` in that
   field. This is the most common cause.
3. **Query Type is wrong.** `Time series` for graphs, `Table` for tables and pies.
4. **A macro that does not exist.** Only `$__unixEpochGroupSeconds` is supported.
   Anything else errors.
5. **Pie shows one number.** Set Value options → Show → **All values**.
6. **Any query errors.** Test the datasource: *Connections → Data sources →
   AzureMentor → Save & test*.
7. **Grafana cannot see the database.**

   ```bash
   docker compose exec grafana ls -la /var/lib/azurementor
   ```

   `monitoring.db` should be listed, and the `./data` mount must be read-write.

8. **`unable to open database file (14)` — the WAL trap.**

   If every panel fails with this, the monitoring database is in **WAL** journal
   mode. WAL needs a shared-memory `-shm` file that SQLite creates with `mmap`,
   and `mmap` does not work on the filesystem Docker Desktop uses for Windows
   bind mounts. Grafana simply cannot open a WAL database there.

   It fails *intermittently*, which makes it hard to recognise: while the app
   container holds the database open the `-shm` file exists and queries succeed;
   once it is checkpointed away they start failing.

   Worse, it is not only a read problem. With `-shm` unavailable SQLite's
   cross-process locking guarantees do not hold, and two processes disagreeing
   about journal mode can lose committed rows — which is exactly what happened
   during development, costing 180 seeded rows.

   `MONITORING_JOURNAL_MODE` therefore defaults to `DELETE`, which needs no
   shared memory and works everywhere. Check it with:

   ```bash
   uv run python -c "import sqlite3; print(sqlite3.connect('data/monitoring.db').execute('PRAGMA journal_mode').fetchone())"
   ```

   If it says `wal`, something is still writing with the old setting — rebuild
   the app image so the container picks up the change:

   ```bash
   docker compose --profile app up -d --build app
   ```

   On a Linux host WAL is safe and gives better concurrency; set
   `MONITORING_JOURNAL_MODE=WAL` there if you want it.

---

## See also

**[README.md](README.md)** — the datasource wiring, the full table schema, and a
larger query cookbook including a few not on the dashboard: relevance over time,
questions the system could not answer, answer quality per prompt template, and
busiest sessions.

Every SQL block in both documents has been executed against the live datasource.
