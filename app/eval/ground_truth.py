"""Synthetic ground truth generation.

Retrieval cannot be measured without knowing which document *should* come back
for a given question. Hand-writing hundreds of those pairs is the usual blocker,
so instead the LLM reads an indexed document and writes questions that document
answers. The document it was generated from is the correct answer by
construction, which is what makes hit rate and MRR computable.

The known weakness: questions generated from a document tend to reuse its
wording, which flatters keyword search. The prompt pushes against this by asking
for a user's phrasing rather than the document's, but the bias cannot be removed
entirely. Treat the absolute numbers as soft and the *comparisons* between
configurations -- which is what the sweep is for -- as sound.
"""

from dataclasses import dataclass
import csv
import json
import logging
import random

from sqlalchemy import func, select
from tqdm import tqdm

from app.config import GROUND_TRUTH_PATH, LLM_MODEL
from app.db.connection import get_session
from app.db.schema import documents as documents_table
from app.llm.client import LLMClient

log = logging.getLogger(__name__)

_SYSTEM = """
You write realistic questions that a person learning Microsoft Azure would type
into a search box.

You are given one page of Azure documentation. Write questions that this page
answers.

Rules:
- Write the way a learner types: natural, specific, sometimes using common
  abbreviations (vm, rbac, nsg). Not documentation headings.
- Each question must be answerable from this page alone.
- Each question must be self-contained. Never write "this service" or "the
  above" -- name the Azure service explicitly, because the question will be used
  without the page attached.
- Do not copy sentences from the page. Rephrase, or the evaluation just measures
  string overlap.
- Vary the shape: how-to, what-is, why, troubleshooting, limits and quotas.

Respond with a JSON object and nothing else:
{"questions": ["...", "..."]}
""".strip()

_USER = """
TITLE: {title}

PAGE:
{content}

Write exactly {count} questions.
""".strip()


@dataclass(slots=True)
class GroundTruthItem:
    question: str
    document_id: int
    url: str
    title: str
    category: str


def generate(
    sample_size: int = 150,
    questions_per_document: int = 3,
    seed: int = 42,
    client: LLMClient | None = None,
    max_content_chars: int = 6000,
) -> list[GroundTruthItem]:
    """Generate a ground truth set from a random sample of indexed documents."""

    client = client or LLMClient(model=LLM_MODEL, temperature=0.7)

    with get_session() as session:

        total = session.execute(
            select(func.count()).select_from(documents_table)
        ).scalar_one()

        if not total:
            raise RuntimeError(
                "No documents in the index. Build it first with "
                "`uv run python -m app.pipeline --fresh`."
            )

        rows = session.execute(
            select(
                documents_table.c.id,
                documents_table.c.title,
                documents_table.c.url,
                documents_table.c.category,
                documents_table.c.content,
            )
        ).all()

    log.info("Sampling %s of %s documents", min(sample_size, len(rows)), total)

    # Seeded so a regenerated set covers the same documents and results stay
    # comparable across runs.
    random.seed(seed)
    sample = random.sample(rows, min(sample_size, len(rows)))

    items: list[GroundTruthItem] = []
    failures = 0

    for row in tqdm(sample, desc="Generating questions", unit="doc"):

        try:
            questions = _questions_for(
                client,
                row.title,
                row.content[:max_content_chars],
                questions_per_document,
            )

        except Exception:
            failures += 1
            log.exception("Question generation failed for document %s", row.id)
            continue

        for question in questions:
            items.append(
                GroundTruthItem(
                    question=question,
                    document_id=row.id,
                    url=row.url,
                    title=row.title,
                    category=row.category,
                )
            )

    log.info(
        "Generated %s questions from %s documents (%s failed)",
        len(items),
        len(sample) - failures,
        failures,
    )

    return items


def _questions_for(
    client: LLMClient,
    title: str,
    content: str,
    count: int,
) -> list[str]:

    response = client.complete(
        _SYSTEM,
        _USER.format(title=title, content=content, count=count),
    )

    text = response.text.strip()

    if text.startswith("```"):
        text = "\n".join(
            line
            for line in text.splitlines()
            if not line.strip().startswith("```")
        ).strip()

    payload = json.loads(text[text.index("{") : text.rindex("}") + 1])

    questions = payload.get("questions", [])

    return [
        question.strip()
        for question in questions
        if isinstance(question, str) and question.strip()
    ]


# --------------------------------------------------
# Persistence
# --------------------------------------------------

FIELDNAMES = ["question", "document_id", "url", "title", "category"]


def save(items: list[GroundTruthItem], path=GROUND_TRUTH_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()

        for item in items:
            writer.writerow(
                {
                    "question": item.question,
                    "document_id": item.document_id,
                    "url": item.url,
                    "title": item.title,
                    "category": item.category,
                }
            )

    log.info("Wrote %s questions to %s", len(items), path)


def load(path=GROUND_TRUTH_PATH) -> list[GroundTruthItem]:
    if not path.exists():
        raise FileNotFoundError(
            f"No ground truth at {path}. Generate it first:\n"
            f"  uv run python -m app.eval.main generate"
        )

    with open(path, newline="", encoding="utf-8") as handle:
        return [
            GroundTruthItem(
                question=row["question"],
                document_id=int(row["document_id"]),
                url=row["url"],
                title=row["title"],
                category=row["category"],
            )
            for row in csv.DictReader(handle)
        ]
