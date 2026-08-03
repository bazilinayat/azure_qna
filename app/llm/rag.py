"""RAG: retrieve, build a prompt, generate an answer.

Same three steps as the Zoomcamp classwork — search, build_prompt, llm — with
two additions this project needs.

First, sources are numbered in the context and the model is told to cite them,
so every answer is traceable back to a Microsoft Learn page. That traceability is
the entire point of building this instead of just asking a chatbot.

Second, every stage is timed and every token counted, and it all comes back in
one AnswerResult. The monitoring dashboard and the LLM evaluation both consume
that object, so neither has to re-run anything to get its numbers.
"""

from dataclasses import dataclass, field
import logging
import re

from app.config import LLM_CONTEXT_CHUNKS, LLM_PROMPT
from app.llm.client import LLMClient, LLMResponse
from app.llm.prompts import PromptTemplate, get_prompt
from app.search.search_result import SearchResult

log = logging.getLogger(__name__)

# Matches the [1] / [2] markers the prompts ask the model to write.
_CITATION = re.compile(r"\[(\d{1,2})\]")


@dataclass(slots=True)
class AnswerResult:
    """A complete answer plus everything needed to monitor or evaluate it."""

    question: str
    answer: str

    sources: list[SearchResult] = field(default_factory=list)

    prompt_name: str = ""

    retrieval_seconds: float = 0.0
    llm_response: LLMResponse | None = None

    @property
    def total_seconds(self) -> float:
        generation = (
            self.llm_response.latency_seconds if self.llm_response else 0.0
        )

        return self.retrieval_seconds + generation

    @property
    def cost_usd(self) -> float | None:
        return self.llm_response.cost_usd if self.llm_response else None

    @property
    def total_tokens(self) -> int:
        return self.llm_response.total_tokens if self.llm_response else 0

    def cited_urls(self) -> list[str]:
        """Unique source URLs, in the order they were given to the model."""

        seen: list[str] = []

        for source in self.sources:
            if source.url not in seen:
                seen.append(source.url)

        return seen

    def cited_indices(self) -> list[int]:
        """1-based indices the answer actually cites, in order of first use."""

        found: list[int] = []

        for match in _CITATION.finditer(self.answer):
            index = int(match.group(1))

            if 1 <= index <= len(self.sources) and index not in found:
                found.append(index)

        return found

    def cited_sources(self) -> list[SearchResult]:
        """Only the sources the answer refers to.

        Retrieval always returns its top-k, so listing all of them under an
        answer implies they were all used. When the model refuses because
        nothing was relevant, showing five 'sources' is actively misleading.
        """

        return [self.sources[index - 1] for index in self.cited_indices()]


def build_context(results: list[SearchResult]) -> str:
    """Render retrieved chunks as numbered, citable sources.

    The numbering is what makes citation possible: the model is told to write
    [2], and [2] maps to a specific URL that can be shown to the user.
    """

    blocks: list[str] = []

    for index, result in enumerate(results, start=1):

        heading = result.header_path or result.title

        blocks.append(
            f"[{index}] {result.title}\n"
            f"Section: {heading}\n"
            f"URL: {result.url}\n\n"
            f"{result.content}"
        )

    return "\n\n---\n\n".join(blocks)


class RagPipeline:
    """Ties retrieval and generation together."""

    def __init__(
        self,
        search=None,
        client: LLMClient | None = None,
        prompt: str | PromptTemplate = LLM_PROMPT,
        context_chunks: int = LLM_CONTEXT_CHUNKS,
    ) -> None:

        self._search = search
        self._client = client

        self.prompt = (
            prompt if isinstance(prompt, PromptTemplate) else get_prompt(prompt)
        )

        self.context_chunks = context_chunks

    # ---------------------------------------------------------
    # Lazily built, so constructing a pipeline costs nothing until it is used.
    # Both load sizeable models or open connections.

    @property
    def search(self):
        if self._search is None:
            from app.search.hybrid_search import HybridSearch

            self._search = HybridSearch()

        return self._search

    @property
    def client(self) -> LLMClient:
        if self._client is None:
            self._client = LLMClient()

        return self._client

    # ---------------------------------------------------------

    def retrieve(self, question: str) -> tuple[list[SearchResult], float]:
        import time

        started = time.perf_counter()

        results = self.search.search(question)

        return results[: self.context_chunks], time.perf_counter() - started

    def answer(self, question: str) -> AnswerResult:
        """Answer one question end to end."""

        question = question.strip()

        if not question:
            raise ValueError("Question is empty.")

        sources, retrieval_seconds = self.retrieve(question)

        if not sources:
            # Nothing retrieved means nothing to ground an answer in. Calling the
            # model anyway would produce exactly the confident, uncited guess
            # this whole system exists to avoid.
            log.info("No results retrieved for %r", question)

            return AnswerResult(
                question=question,
                answer=(
                    "I could not find anything about that in the indexed Azure "
                    "documentation, so I have nothing to answer from. Try "
                    "rephrasing, or check whether the service you are asking "
                    "about is included in the current index."
                ),
                sources=[],
                prompt_name=self.prompt.name,
                retrieval_seconds=retrieval_seconds,
            )

        context = build_context(sources)

        system, user = self.prompt.render(question, context)

        llm_response = self.client.complete(system, user)

        log.debug(
            "Answered %r using %s sources in %.2fs",
            question,
            len(sources),
            retrieval_seconds + llm_response.latency_seconds,
        )

        return AnswerResult(
            question=question,
            answer=llm_response.text,
            sources=sources,
            prompt_name=self.prompt.name,
            retrieval_seconds=retrieval_seconds,
            llm_response=llm_response,
        )
