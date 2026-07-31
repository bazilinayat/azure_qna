from sqlalchemy import (
    MetaData,
    Table,
    Column,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    DateTime,
    func,
)

metadata = MetaData()

# --------------------------------------------------
# Documents
# --------------------------------------------------

documents = Table(
    "documents",
    metadata,

    Column("id", Integer, primary_key=True),

    Column("title", String(500), nullable=False),

    # Frontmatter description. Short, human-written summary of the article.
    Column("description", Text),

    Column("url", String(1000), nullable=False, unique=True),

    # Top-level service folder, e.g. "storage", "app-service".
    Column("category", String(200), nullable=False),

    # Which upstream repository this document came from.
    Column("source_repo", String(200), nullable=False),

    # Path of the source markdown file, relative to the articles directory.
    Column("source_path", String(1000), nullable=False),

    Column("content", Text, nullable=False),

    Column("last_updated", String(100)),

    Column(
        "created_at",
        DateTime,
        server_default=func.now(),
        nullable=False,
    ),

    Column(
        "updated_at",
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    ),
)

# --------------------------------------------------
# Chunks
# --------------------------------------------------

chunks = Table(
    "chunks",
    metadata,

    Column("id", Integer, primary_key=True),

    Column(
        "document_id",
        Integer,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    ),

    Column(
        "chunk_index",
        Integer,
        nullable=False,
    ),

    # Breadcrumb of markdown headers this chunk sits under, e.g.
    # "Introduction to Azure Blob Storage > Blob Storage resources > Containers".
    # Prepended to the chunk body so both BM25 and the embedding see the context.
    Column(
        "header_path",
        Text,
    ),

    Column(
        "content",
        Text,
        nullable=False,
    ),

    # Real token count, measured with the embedding model's tokenizer.
    Column(
        "token_count",
        Integer,
        nullable=False,
    ),

    # Set once the chunk has been embedded and upserted into Qdrant. Doubles as
    # the resume marker so an interrupted indexing run can pick up where it
    # stopped instead of starting over.
    Column(
        "embedding_model",
        String(100),
    ),

    Column(
        "created_at",
        DateTime,
        server_default=func.now(),
        nullable=False,
    ),
)

# --------------------------------------------------
# Indexes
# --------------------------------------------------

Index("idx_documents_title", documents.c.title)

Index("idx_documents_category", documents.c.category)

Index("idx_chunks_document", chunks.c.document_id)

Index("idx_chunks_chunk_index", chunks.c.chunk_index)

# Supports the "which chunks still need embedding?" resume query.
Index("idx_chunks_embedding_model", chunks.c.embedding_model)
