from dataclasses import dataclass


@dataclass(slots=True)
class SearchResult:

    id: int

    document_id: int

    chunk_index: int

    title: str

    url: str

    category: str

    # Markdown header breadcrumb this chunk sits under.
    header_path: str | None

    content: str

    score: float

    # Which retriever produced this result: "keyword", "vector", "rrf" or
    # "reranker". Useful for both debugging and the monitoring dashboard.
    source: str
