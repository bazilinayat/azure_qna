"""Generate the provisioned Grafana dashboard JSON."""

import json
import pathlib

DS = {"type": "frser-sqlite-datasource", "uid": "azurementor-sqlite"}
TF = "created_at_unix BETWEEN $__from/1000 AND $__to/1000"


def target(sql, qtype="table", timecols=None):
    return {
        "refId": "A",
        "datasource": DS,
        "queryText": sql,
        "rawQueryText": sql,
        "queryType": qtype,
        "timeColumns": timecols or [],
    }


def panel(pid, title, ptype, x, y, w, h, sql, qtype="table", timecols=None,
          unit=None, options=None, desc=None, overrides=None):
    p = {
        "id": pid,
        "title": title,
        "type": ptype,
        "datasource": DS,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "targets": [target(sql, qtype, timecols)],
        "fieldConfig": {"defaults": {}, "overrides": overrides or []},
        "options": options or {},
    }
    if unit:
        p["fieldConfig"]["defaults"]["unit"] = unit
    if desc:
        p["description"] = desc
    return p


TS_OPTS = {
    "legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
    "tooltip": {"mode": "multi", "sort": "none"},
}


def ts_panel(pid, title, x, y, w, h, sql, unit=None, desc=None, style="line"):
    p = panel(pid, title, "timeseries", x, y, w, h, sql, "time series", ["time"],
              unit=unit, options=dict(TS_OPTS), desc=desc)
    p["fieldConfig"]["defaults"]["custom"] = {
        "drawStyle": style, "lineWidth": 2, "fillOpacity": 10,
        "showPoints": "auto", "spanNulls": True,
    }
    return p


def stat_panel(pid, title, x, y, w, h, sql, unit=None, desc=None,
               color="value", overrides=None):
    return panel(
        pid, title, "stat", x, y, w, h, sql, "table", None,
        unit=unit, desc=desc, overrides=overrides,
        options={
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "textMode": "auto", "colorMode": color,
            "graphMode": "none", "justifyMode": "auto",
        },
    )


panels = []

# Row 1 - headline stats
panels.append(stat_panel(
    1, "Questions asked", 0, 0, 6, 4,
    "SELECT COUNT(*) AS questions FROM conversations WHERE " + TF,
    desc="Total questions answered in the selected time range."))

panels.append(stat_panel(
    2, "Avg response time", 6, 0, 6, 4,
    "SELECT ROUND(AVG(total_seconds), 2) AS seconds FROM conversations WHERE " + TF,
    unit="s", desc="Retrieval plus generation, averaged."))

panels.append(stat_panel(
    3, "Judged relevant", 12, 0, 6, 4,
    "SELECT ROUND(100.0 * SUM(CASE WHEN relevance = 'RELEVANT' THEN 1 ELSE 0 END) "
    "/ COUNT(*), 1) AS percent FROM conversations "
    "WHERE relevance IS NOT NULL AND " + TF,
    unit="percent", desc="Share of answers the LLM judge rated RELEVANT."))

panels.append(stat_panel(
    4, "Tokens used", 18, 0, 6, 4,
    "SELECT SUM(total_tokens) AS tokens FROM conversations WHERE " + TF,
    desc="Prompt plus completion tokens."))

# Row 2 - lesson panels 1 and 2
panels.append(ts_panel(
    5, "Response time", 0, 4, 12, 8,
    "SELECT $__unixEpochGroupSeconds(created_at_unix, 3600) AS time,\n"
    "       ROUND(AVG(retrieval_seconds), 3)  AS retrieval,\n"
    "       ROUND(AVG(generation_seconds), 3) AS generation\n"
    "FROM conversations\nWHERE " + TF + "\nGROUP BY 1\nORDER BY 1",
    unit="s",
    desc="Split by stage, so a slowdown can be attributed to search or to the LLM."))

panels.append(ts_panel(
    6, "Token usage", 12, 4, 12, 8,
    "SELECT $__unixEpochGroupSeconds(created_at_unix, 3600) AS time,\n"
    "       ROUND(AVG(prompt_tokens))     AS prompt,\n"
    "       ROUND(AVG(completion_tokens)) AS completion\n"
    "FROM conversations\nWHERE " + TF + "\nGROUP BY 1\nORDER BY 1",
    desc="Average tokens per question. Prompt tokens dominate and are driven by "
         "LLM_CONTEXT_CHUNKS."))

# Row 3 - lesson panel 3, plus volume
# Hourly, not daily. A daily bucket is stamped at midnight UTC, which falls
# outside any short dashboard range, so the panel reads "Data outside time range"
# on load even though the rows are there.
panels.append(ts_panel(
    7, "Cost", 0, 12, 12, 8,
    "SELECT $__unixEpochGroupSeconds(created_at_unix, 3600) AS time,\n"
    "       ROUND(SUM(COALESCE(cost_usd, 0)), 6) AS cost\n"
    "FROM conversations\nWHERE " + TF + "\nGROUP BY 1\nORDER BY 1",
    unit="currencyUSD", style="bars",
    desc="Zero while the model's price is unknown. Set LLM_PRICE_INPUT_PER_1M and "
         "LLM_PRICE_OUTPUT_PER_1M in .env to populate this."))

panels.append(ts_panel(
    8, "Questions per hour", 12, 12, 12, 8,
    "SELECT $__unixEpochGroupSeconds(created_at_unix, 3600) AS time,\n"
    "       COUNT(*) AS questions\n"
    "FROM conversations\nWHERE " + TF + "\nGROUP BY 1\nORDER BY 1",
    style="bars", desc="Traffic volume over time."))

# Row 4 - lesson panels 4, 5, 6
p = panel(
    9, "Model usage", "piechart", 0, 20, 8, 8,
    "SELECT model, COUNT(*) AS count\nFROM conversations\nWHERE " + TF +
    "\nGROUP BY model",
    options={
        "legend": {"displayMode": "list", "placement": "right", "showLegend": True},
        "pieType": "donut",
        "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": True},
    },
    desc="Which answering model served the traffic.")
panels.append(p)

p = panel(
    10, "Relevance distribution", "piechart", 8, 20, 8, 8,
    "SELECT COALESCE(relevance, 'UNJUDGED') AS relevance, COUNT(*) AS count\n"
    "FROM conversations\nWHERE " + TF + "\nGROUP BY 1",
    options={
        "legend": {"displayMode": "list", "placement": "right", "showLegend": True},
        "pieType": "pie",
        "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": True},
    },
    desc="The LLM judge's verdict on every answer served.",
    overrides=[
        {"matcher": {"id": "byName", "options": "RELEVANT"},
         "properties": [{"id": "color",
                         "value": {"mode": "fixed", "fixedColor": "green"}}]},
        {"matcher": {"id": "byName", "options": "PARTLY_RELEVANT"},
         "properties": [{"id": "color",
                         "value": {"mode": "fixed", "fixedColor": "orange"}}]},
        {"matcher": {"id": "byName", "options": "NON_RELEVANT"},
         "properties": [{"id": "color",
                         "value": {"mode": "fixed", "fixedColor": "red"}}]},
    ])
panels.append(p)

panels.append(stat_panel(
    11, "User feedback", 16, 20, 8, 8,
    'SELECT SUM(CASE WHEN value =  1 THEN 1 ELSE 0 END) AS "thumbs up",\n'
    '       SUM(CASE WHEN value = -1 THEN 1 ELSE 0 END) AS "thumbs down"\n'
    "FROM feedback\nWHERE " + TF,
    color="background",
    desc="Thumbs collected in the app.",
    overrides=[
        {"matcher": {"id": "byName", "options": "thumbs up"},
         "properties": [{"id": "color",
                         "value": {"mode": "fixed", "fixedColor": "green"}}]},
        {"matcher": {"id": "byName", "options": "thumbs down"},
         "properties": [{"id": "color",
                         "value": {"mode": "fixed", "fixedColor": "red"}}]},
    ]))

# Row 5 - lesson panel 7
p = panel(
    12, "Recent conversations", "table", 0, 28, 24, 10,
    "SELECT created_at, question, COALESCE(relevance, 'UNJUDGED') AS relevance,\n"
    "       ROUND(total_seconds, 2) AS seconds, total_tokens AS tokens,\n"
    "       num_sources_cited AS cited\n"
    "FROM conversations\nWHERE " + TF +
    "\nORDER BY created_at_unix DESC\nLIMIT 20",
    options={"showHeader": True, "cellHeight": "sm",
             "footer": {"show": False, "reducer": ["sum"], "countRows": False,
                        "fields": ""}},
    desc="Newest first.")
panels.append(p)

# Row 6 - beyond the lesson: where the judge and the user disagree
p = panel(
    13, "Judge and user disagree", "table", 0, 38, 24, 8,
    "SELECT c.created_at, c.question, c.relevance,\n"
    "       CASE WHEN f.value = 1 THEN 'thumbs up' ELSE 'thumbs down' END AS user_vote,\n"
    "       c.relevance_explanation\n"
    "FROM conversations c\n"
    "JOIN feedback f ON f.conversation_id = c.id\n"
    "WHERE ((f.value = -1 AND c.relevance = 'RELEVANT')\n"
    "    OR (f.value =  1 AND c.relevance = 'NON_RELEVANT'))\n"
    "  AND c." + TF + "\nORDER BY c.created_at_unix DESC",
    options={"showHeader": True, "cellHeight": "sm"},
    desc="Answers the judge liked but users did not, and the reverse. This is "
         "where prompt problems surface.")
panels.append(p)

dashboard = {
    "uid": "azurementor-monitoring",
    "title": "AzureMentor monitoring",
    "description": "Live traffic, quality and cost for the AzureMentor RAG app.",
    "tags": ["azurementor", "llm", "rag"],
    "timezone": "browser",
    "schemaVersion": 39,
    "version": 1,
    "editable": True,
    "refresh": "30s",
    # 24h rather than 6h: a low-volume app has long quiet stretches, and a
    # dashboard that opens on an empty window looks broken.
    "time": {"from": "now-24h", "to": "now"},
    "panels": panels,
}

out = pathlib.Path("grafana/provisioning/dashboards/azurementor.json")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(dashboard, indent=2), encoding="utf-8")

json.loads(out.read_text(encoding="utf-8"))
print(f"wrote {out} - {len(panels)} panels, valid JSON")
