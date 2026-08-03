# AzureMentor Streamlit app.
#
# The index itself is NOT baked in: at ~430 MB of SQLite plus 1.4 GB of Qdrant
# storage it would make the image unusable. Both are mounted at runtime, so the
# image stays small and rebuilding the index does not mean rebuilding the image.

FROM python:3.13-slim

# uv resolves and installs far faster than pip, and the lock file is already
# uv's, so using anything else here would mean maintaining a second one.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    HF_HOME=/app/.cache/huggingface

# Dependencies first, as their own layer: application code changes far more
# often than the lock file, and this keeps rebuilds to seconds.
#
# The cache is cleared in the same RUN as the install. Docker layers are
# append-only, so removing it in a later step would leave the 1.5 GB of
# downloaded wheels in the image anyway -- it has to go before the layer closes.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev \
 && uv cache clean \
 && rm -rf /root/.cache/uv

COPY app ./app

RUN uv sync --frozen --no-dev \
 && uv cache clean \
 && rm -rf /root/.cache/uv

# Bake the embedding and reranking models into the image. Without this the
# first question after every container start pays a ~250 MB download, which
# looks like a hang to whoever is using it.
#
# Only the safetensors weights are fetched. Hugging Face repos ship the same
# model several times over -- PyTorch .bin, ONNX, OpenVINO -- and pulling all of
# them roughly triples this layer for files that are never loaded.
RUN uv run python -c "\
from sentence_transformers import SentenceTransformer, CrossEncoder; \
SentenceTransformer('BAAI/bge-small-en-v1.5'); \
CrossEncoder('cross-encoder/ms-marco-MiniLM-L6-v2'); \
print('models cached')" \
 && find /app/.cache/huggingface -type d -name onnx -prune -exec rm -rf {} + 2>/dev/null || true

EXPOSE 8501

# Streamlit's own health endpoint, so compose can tell running from ready.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')"

CMD ["uv", "run", "streamlit", "run", "app/ui/streamlit_app.py", \
     "--server.port=8501", \
     "--server.address=0.0.0.0", \
     "--server.headless=true", \
     "--browser.gatherUsageStats=false"]
