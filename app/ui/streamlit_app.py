"""AzureMentor chat interface.

Chat history lives only in Streamlit's session state: reload the tab and it is
gone. That is deliberate and the banner says so, because a user who does not
know their history is disposable will lose work they wanted.

What *is* persisted is the monitoring record for each answer — question, answer,
latency, tokens, cost, relevance and feedback. Those go to a separate database
that Grafana reads. The distinction matters and is stated in the sidebar rather
than buried: the conversation is not kept, the metrics are.
"""

import sys
import uuid
from pathlib import Path

import streamlit as st

# Allow `streamlit run app/ui/streamlit_app.py` from the repo root without
# needing the package to be installed.
ROOT = Path(__file__).resolve().parent.parent.parent

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import (  # noqa: E402
    CHUNK_PROFILE,
    DATABASE_PATH,
    JUDGE_LIVE_ANSWERS,
    LLM_MODEL,
    LLM_PROMPT,
    MONITORING_ENABLED,
)
from app.llm.prompts import PROMPTS  # noqa: E402
from app.ui import export  # noqa: E402

st.set_page_config(
    page_title="AzureMentor",
    page_icon="☁️",
    layout="centered",
)


# --------------------------------------------------
# Cached singletons
# --------------------------------------------------
# Streamlit re-runs this whole script on every interaction, so without caching
# the embedder and cross-encoder would reload on every keystroke.

@st.cache_resource(show_spinner="Loading models (first run takes a moment)...")
def get_pipeline(prompt_name: str):
    from app.llm.rag import RagPipeline

    return RagPipeline(prompt=prompt_name)


@st.cache_resource
def get_judge():
    from app.eval.judge import RelevanceJudge

    return RelevanceJudge()


@st.cache_resource
def init_monitoring() -> bool:
    if not MONITORING_ENABLED:
        return False

    try:
        from app.monitoring.store import init_database

        init_database()
        return True

    except Exception as exc:  # pragma: no cover - defensive
        st.warning(f"Monitoring is unavailable: {exc}")
        return False


# --------------------------------------------------
# Session state
# --------------------------------------------------

if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())

if "messages" not in st.session_state:
    st.session_state.messages = []

if "feedback_given" not in st.session_state:
    st.session_state.feedback_given = {}


# --------------------------------------------------
# Sidebar
# --------------------------------------------------

with st.sidebar:
    st.subheader("Session")

    st.caption(f"Session `{st.session_state.session_id[:8]}`")

    prompt_name = st.selectbox(
        "Answer style",
        options=list(PROMPTS),
        index=list(PROMPTS).index(LLM_PROMPT) if LLM_PROMPT in PROMPTS else 0,
        help="Prompt template. The evaluation compares these.",
    )

    st.caption(PROMPTS[prompt_name].description)

    st.divider()

    st.subheader("Save this chat")

    if st.session_state.messages:
        st.download_button(
            "Download as Word",
            data=export.to_docx(st.session_state.messages),
            file_name=export.default_filename("docx"),
            mime=(
                "application/vnd.openxmlformats-officedocument"
                ".wordprocessingml.document"
            ),
            use_container_width=True,
        )

        st.download_button(
            "Download as PDF",
            data=export.to_pdf(st.session_state.messages),
            file_name=export.default_filename("pdf"),
            mime="application/pdf",
            use_container_width=True,
        )

        if st.button("Clear chat", use_container_width=True):
            st.session_state.messages = []
            st.session_state.feedback_given = {}
            st.rerun()

    else:
        st.caption("Ask something first.")

    st.divider()

    st.caption(
        f"model `{LLM_MODEL}`  \n"
        f"index `{CHUNK_PROFILE}`  \n"
        f"judge {'on' if JUDGE_LIVE_ANSWERS else 'off'}  \n"
        f"monitoring {'on' if MONITORING_ENABLED else 'off'}"
    )


# --------------------------------------------------
# Header
# --------------------------------------------------

st.title("☁️ AzureMentor")

st.caption(
    "Ask anything about Microsoft Azure. Answers come from the official "
    "documentation, with links to every page used."
)

st.warning(
    "**This chat is not saved.** Your conversation lives only in this browser "
    "tab — refresh or close it and everything is gone. Download it as Word or "
    "PDF from the sidebar if you want to keep it.",
    icon="⚠️",
)

if not DATABASE_PATH.exists():
    st.error(
        f"No search index found at `{DATABASE_PATH}`.\n\n"
        "Build one first:\n\n"
        "```\nuv run python -m app.pipeline --fresh\n```"
    )
    st.stop()

monitoring_ready = init_monitoring()


# --------------------------------------------------
# History
# --------------------------------------------------

def render_sources(sources: list[dict]) -> None:
    if not sources:
        return

    with st.expander(f"{len(sources)} sources"):
        for index, source in enumerate(sources, start=1):
            st.markdown(f"**[{index}] {source['title']}**")

            if source.get("header_path"):
                st.caption(source["header_path"])

            st.markdown(f"[{source['url']}]({source['url']})")


def render_feedback(message_index: int, conversation_id: str | None) -> None:
    """Thumbs up/down. Recorded once per answer."""

    if not conversation_id or not monitoring_ready:
        return

    key = str(message_index)

    if key in st.session_state.feedback_given:
        vote = st.session_state.feedback_given[key]
        st.caption("Thanks for the feedback." if vote > 0 else "Thanks — noted.")
        return

    from app.monitoring.store import log_feedback

    left, right, _ = st.columns([1, 1, 8])

    if left.button("👍", key=f"up-{message_index}"):
        log_feedback(conversation_id, 1)
        st.session_state.feedback_given[key] = 1
        st.rerun()

    if right.button("👎", key=f"down-{message_index}"):
        log_feedback(conversation_id, -1)
        st.session_state.feedback_given[key] = -1
        st.rerun()


for index, message in enumerate(st.session_state.messages):

    with st.chat_message(message["role"]):
        st.markdown(message["content"])

        if message["role"] == "assistant":
            render_sources(message.get("sources", []))
            render_feedback(index, message.get("conversation_id"))


# --------------------------------------------------
# Input
# --------------------------------------------------

question = st.chat_input("Ask about Azure...")

if question:

    st.session_state.messages.append({"role": "user", "content": question})

    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):

        with st.spinner("Searching the documentation..."):
            try:
                pipeline = get_pipeline(prompt_name)
                result = pipeline.answer(question)

            except Exception as exc:
                st.error(f"Something went wrong: {type(exc).__name__}: {exc}")
                st.stop()

        st.markdown(result.answer)

        sources = [
            {
                "title": result.sources[number - 1].title,
                "url": result.sources[number - 1].url,
                "header_path": result.sources[number - 1].header_path,
            }
            for number in result.cited_indices()
        ]

        render_sources(sources)

        # Judging is a second API call, so it happens after the answer is on
        # screen. The user is never left waiting on a step that is for our
        # benefit rather than theirs.
        judgement = None

        if JUDGE_LIVE_ANSWERS and result.llm_response is not None:
            try:
                judgement = get_judge().judge(question, result.answer)

            except Exception:
                judgement = None

        conversation_id = None

        if monitoring_ready:
            from app.monitoring.store import log_conversation

            conversation_id = log_conversation(
                result,
                session_id=st.session_state.session_id,
                judgement=judgement,
            )

        st.session_state.messages.append(
            {
                "role": "assistant",
                "content": result.answer,
                "sources": sources,
                "conversation_id": conversation_id,
            }
        )

        if result.llm_response is not None:
            llm = result.llm_response

            cost = (
                f" · ${llm.cost_usd:.4f}" if llm.cost_usd is not None else ""
            )

            st.caption(
                f"{result.total_seconds:.1f}s · {llm.total_tokens} tokens{cost}"
            )

    st.rerun()
