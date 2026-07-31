from app.embedding.embedder import get_embedder
from app.embedding.qdrant_client import get_repository
from app.search.search_result import SearchResult


class VectorSearch:
    """Dense retrieval over the Qdrant collection."""

    def __init__(self) -> None:
        # Shared singletons: loading the ONNX session per component was costing
        # several seconds and hundreds of MB for no benefit.
        self.embedder = get_embedder()
        self.repository = get_repository()

    def search(self, query: str, limit: int = 20) -> list[SearchResult]:

        # embed_query, not embed: bge applies an instruction prefix on the query
        # side only. Embedding a query like a passage measurably degrades recall.
        vector = self.embedder.embed_query(query)

        hits = self.repository.search(vector, limit)

        return [
            SearchResult(
                id=hit.id,
                document_id=hit.payload["document_id"],
                chunk_index=hit.payload["chunk_index"],
                title=hit.payload["title"],
                url=hit.payload["url"],
                category=hit.payload["category"],
                header_path=hit.payload.get("header_path"),
                content=hit.payload["content"],
                score=hit.score,
                source="vector",
            )
            for hit in hits
        ]
