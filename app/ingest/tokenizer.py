"""Token counting in the *embedding model's* token space.

This matters more than it looks. bge-small-en-v1.5 truncates input at 512
tokens, and its WordPiece tokenizer emits roughly 1.27x as many tokens as
tiktoken's cl100k_base on Azure documentation (identifiers, hyphenated resource
names and URLs all get shredded into subwords). Chunking to "512 cl100k tokens"
therefore produces chunks of ~650 model tokens, and the embedding model silently
discards everything past 512 — about a fifth of every full chunk, invisibly.

So chunk sizes are measured with the real tokenizer wherever possible.

Only counting is exposed, never decoding: the bge vocabulary is uncased, so
decoding round-trips would lowercase and mangle the stored text.
"""

import logging
from functools import lru_cache

from app.config import EMBEDDING_MODEL

log = logging.getLogger(__name__)

# Applied when falling back to tiktoken, to approximate WordPiece inflation.
_TIKTOKEN_SAFETY_FACTOR = 1.3


class TokenCounter:
    """Counts tokens the way the embedding model will count them."""

    def __init__(self) -> None:
        self._backend = None
        self._kind = "none"

        self._load()

    def _load(self) -> None:
        try:
            from transformers import AutoTokenizer

            tokenizer = AutoTokenizer.from_pretrained(EMBEDDING_MODEL)

            # The `tokenizers` backend has no model_max_length warning and
            # encodes noticeably faster than the Python wrapper.
            self._backend = tokenizer.backend_tokenizer
            self._kind = "model"

            log.info("Token counter: %s tokenizer", EMBEDDING_MODEL)
            return

        except Exception as exc:
            log.warning(
                "Could not load the %s tokenizer (%s). "
                "Falling back to tiktoken with a %.2fx safety factor.",
                EMBEDDING_MODEL,
                exc,
                _TIKTOKEN_SAFETY_FACTOR,
            )

        import tiktoken

        self._backend = tiktoken.get_encoding("cl100k_base")
        self._kind = "tiktoken"

    @property
    def kind(self) -> str:
        return self._kind

    def count(self, text: str) -> int:
        if not text:
            return 0

        if self._kind == "model":
            return len(
                self._backend.encode(text, add_special_tokens=False).ids
            )

        return int(
            len(self._backend.encode(text)) * _TIKTOKEN_SAFETY_FACTOR
        )

    def count_many(self, texts: list[str]) -> list[int]:
        """Batch version. Much faster than calling `count` in a loop."""

        if not texts:
            return []

        if self._kind == "model":
            encodings = self._backend.encode_batch(
                texts,
                add_special_tokens=False,
            )

            return [len(encoding.ids) for encoding in encodings]

        return [self.count(text) for text in texts]


@lru_cache(maxsize=1)
def get_token_counter() -> TokenCounter:
    """Return the process-wide token counter."""

    return TokenCounter()
