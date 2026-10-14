"""Streamlit UI for asking questions about an indexed repository.

Run with:  uv run streamlit run app/ui/streamlit_app.py

The UI only gathers input and renders results; indexing, retrieval, answer
generation and investigations all go through the same functions the CLI uses.

Tabs:
- Quick answer: one retrieval + one LLM call (Phase 3 answer generator).
- Investigate: multi-turn investigations with project and GitHub tools, run
  through a real MCP session (Phase 6 orchestrator).
"""

from pathlib import Path

import streamlit as st

from app.config import Settings, get_settings
from app.integrations.github_client import resolve_repo
from app.integrations.github_errors import GitHubError
from app.llm.base import LLMError
from app.orchestration.assistant import (
    EvidenceSource,
    InvestigationResult,
    Investigator,
    OrchestrationConfigError,
    SessionConfig,
    build_tool_llm,
    unwrap_untrusted,
)
from app.orchestration.tool_backends import MCPClientBackend
from app.rag.answer_generator import Answer, AnswerGenerator
from app.retrieval.embeddings import EmbeddingProvider, get_embedding_provider
from app.retrieval.indexer import IndexStats, build_index, index_stats
from app.retrieval.retriever import InvalidQueryError
from app.retrieval.vector_store import ProjectNotIndexedError, VectorStoreError

st.set_page_config(page_title="AI Developer Assistant", layout="wide")


@st.cache_resource(show_spinner="Loading embedding model...")
def cached_embedding_provider(provider: str, model: str, batch_size: int) -> EmbeddingProvider:
    """One embedding model per session, shared across reruns."""
    settings = get_settings(embedding_provider=provider, embedding_model=model, embedding_batch_size=batch_size)
    return get_embedding_provider(settings)


def embedding_provider_for(settings: Settings) -> EmbeddingProvider:
    return cached_embedding_provider(
        settings.embedding_provider, settings.embedding_model, settings.embedding_batch_size
    )


def load_stats(settings: Settings, project: Path) -> IndexStats | None:
    try:
        return index_stats(settings, project)
    except ProjectNotIndexedError:
        return None


def sidebar(settings: Settings) -> tuple[Path, int, list[str]]:
    st.sidebar.header("Project")
    project = Path(st.sidebar.text_input("Project path", value=str(settings.project_root.resolve())))
    if not project.is_dir():
        st.sidebar.error("This path is not a directory.")
        st.stop()

    stats = load_stats(settings, project)
    label = "Rebuild index" if stats else "Index project"
    if st.sidebar.button(label, width="stretch"):
        with st.sidebar.status("Indexing...", expanded=True) as status:
            try:
                report = build_index(project, rebuild=True, settings=settings,
                                     provider=embedding_provider_for(settings))
            except (VectorStoreError, NotADirectoryError, ValueError) as exc:
                status.update(label="Indexing failed", state="error")
                st.error(str(exc))
            else:
                status.update(label="Indexed", state="complete")
                st.write(f"**{report.chunks_indexed}** chunks from **{report.files_indexed}** files "
                         f"in {report.duration_seconds:.1f}s")
                st.json(report.chunks_per_file_type)
                stats = load_stats(settings, project)

    if stats:
        st.sidebar.caption(f"Collection `{stats.collection_name}`")
        col1, col2 = st.sidebar.columns(2)
        col1.metric("Chunks", stats.chunk_count)
        col2.metric("Files", len(stats.files))
        st.sidebar.caption(f"Embeddings: `{stats.model_name}`")
        with st.sidebar.expander("Indexed files"):
            st.write("\n".join(f"- `{f}`" for f in stats.files))
    else:
        st.sidebar.warning("This project is not indexed yet.")

    st.sidebar.header("Retrieval")
    top_k = st.sidebar.slider("Chunks to retrieve (top_k)", 1, 20, settings.retrieval_top_k)
    types = sorted({ext.lstrip(".") for ext in settings.supported_extensions} | {"env"})
    file_types = st.sidebar.multiselect("File types (empty = all)", types)

    st.sidebar.header("LLM")
    st.sidebar.caption(f"Provider: `{settings.llm_provider}`")
    st.sidebar.caption(f"Model: `{settings.llm_model or 'not set'}`")
    return project, top_k, file_types


def render_answer(answer: Answer) -> None:
    if answer.insufficient_evidence:
        st.warning("**Insufficient evidence**: the project sources don't fully answer this question.")
    st.markdown(answer.answer_text)
    for suggestion in answer.suggestions:
        st.markdown(f"- {suggestion}")
    if answer.invalid_citations:
        st.error(
            "Invalid citations: the answer references "
            + ", ".join(f"`[{c}]`" for c in answer.invalid_citations)
            + ", which were not among the provided sources. Treat those claims with suspicion."
        )

    st.subheader("Sources")
    if answer.cited_sources:
        for src in answer.cited_sources:
            st.markdown(f"**[{src.source_id}]** `{src.file_path}` ({src.location}, score {src.score:.3f})")
    else:
        st.caption("No sources cited.")

    if answer.all_retrieved_sources:
        st.subheader("Retrieved chunks")
        for chunk in answer.all_retrieved_sources:
            with st.expander(f"{chunk.file_path} · chunk {chunk.chunk_index} · score {chunk.score:.3f}"):
                st.code(chunk.content, language=None)
    if answer.dropped_sources:
        st.caption(f"{len(answer.dropped_sources)} chunk(s) dropped to fit the context budget.")

    lat = answer.latency
    tokens = f"{answer.input_tokens} in / {answer.output_tokens} out" if answer.model else "no LLM call"
    st.caption(
        f"Model `{answer.model or '-'}` · tokens {tokens} · prompt `{answer.prompt_version}` · "
        f"retrieval {lat.retrieval_ms:.0f} ms · LLM {lat.llm_ms:.0f} ms · total {lat.total_ms:.0f} ms"
    )


def quick_answer_tab(settings: Settings, project: Path, top_k: int, file_types: list[str]) -> None:
    st.caption("One retrieval step and one LLM call. Answers cite the project files they rely on.")
    with st.form("ask"):
        question = st.text_area("Question", placeholder="How does authentication work?")
        submitted = st.form_submit_button("Ask")
    if not submitted:
        return

    generator = AnswerGenerator(settings, embedding_provider=embedding_provider_for(settings))
    try:
        with st.spinner("Retrieving sources and generating an answer..."):
            answer = generator.answer(question, project, top_k=top_k, file_types=file_types or None)
    except ProjectNotIndexedError:
        st.error("This project is not indexed yet. Use **Index project** in the sidebar first.")
    except (InvalidQueryError, LLMError, VectorStoreError) as exc:
        st.error(str(exc))
    else:
        render_answer(answer)


# ---- Investigate tab --------------------------------------------------------------------------


def _close_investigation() -> None:
    current = st.session_state.pop("investigation", None)
    if current:
        current["backend"].close()


def get_investigator(settings: Settings, project: Path, repo: str | None, mode: str) -> Investigator:
    """One investigator (and MCP server process) per session configuration, kept across reruns."""
    key = (str(project), repo, mode)
    current = st.session_state.get("investigation")
    if current and current["key"] == key:
        return current["investigator"]
    _close_investigation()
    st.session_state["turns"] = []
    llm = build_tool_llm(settings)
    with st.spinner("Starting the MCP tool server..."):
        backend = MCPClientBackend(settings, project)
    try:
        investigator = Investigator(settings, llm, backend, SessionConfig(project_path=project, repo=repo, mode=mode))
    except Exception:
        backend.close()
        raise
    st.session_state["investigation"] = {"key": key, "investigator": investigator, "backend": backend}
    return investigator


def render_sources(title: str, sources: list[EvidenceSource]) -> None:
    cited = [s for s in sources if s.cited]
    with st.expander(f"{title} ({len(cited)} cited of {len(sources)} found)", expanded=bool(cited)):
        if not sources:
            st.caption("None.")
        for src in sources:
            mark = "" if src.cited else " · *not cited*"
            if src.kind == "project":
                score = f" · score {src.score:.3f}" if src.score is not None else ""
                st.markdown(f"**[{src.cite_id}]** `{src.file_path}` ({src.location}){score}{mark}")
            else:
                kind = "PR" if src.is_pull_request else "Issue"
                label = unwrap_untrusted(src.label)  # display only; still untrusted text
                st.markdown(f"**[{src.cite_id}]** {kind} [{label}]({src.url}){mark}")


def render_investigation(result: InvestigationResult) -> None:
    if result.limits_hit:
        st.warning(f"Limits reached: {', '.join(result.limits_hit)}. The answer uses the evidence gathered so far.")
    if result.github_disabled:
        st.warning("GitHub was rate limited, so further GitHub calls were skipped for this question.")
    st.markdown(result.answer_text)
    if result.invalid_citations:
        st.error("Invalid citations (not returned by any tool): "
                 + ", ".join(f"`[{c}]`" for c in result.invalid_citations))
    col1, col2 = st.columns(2)
    with col1:
        render_sources("Project sources", result.project_sources)
    with col2:
        render_sources("GitHub sources", result.github_sources)
    with st.expander("Investigation trace"):
        for t in result.trace:
            if t.kind == "llm":
                st.markdown(f"`{t.step}` LLM ({t.status}) · {t.duration_ms / 1000:.1f}s · "
                            f"tokens {t.input_tokens}/{t.output_tokens}")
            else:
                err = f" · **{t.error_category}**" if t.error_category else ""
                st.markdown(f"`{t.step}` tool `{t.name}` · {t.status} · {t.duration_ms / 1000:.2f}s · "
                            f"args `{t.args}`{err}")
    lat = result.latency
    st.caption(f"{result.tool_calls} tool calls ({result.cached_calls} cached, {result.rejected_calls} rejected) · "
               f"{result.llm_calls} LLM calls · tokens {result.input_tokens} in / {result.output_tokens} out · "
               f"total {lat.total_ms / 1000:.1f}s · `{result.model}` · prompt `{result.prompt_version}`")


def investigate_tab(settings: Settings, project: Path) -> None:
    st.caption("Multi-turn investigations. The assistant decides whether to search the project, GitHub, both or "
               "neither, using read-only tools through the MCP server.")
    col1, col2, col3 = st.columns([1, 2, 1])
    mode = col1.selectbox("Mode", ["auto", "project", "github"])
    repo_input = col2.text_input("GitHub repository (owner/name)", value=settings.github_default_repo or "",
                                 help="Must be on GITHUB_ALLOWED_REPOS. Leave empty for project-only.")
    if col3.button("New conversation", width="stretch"):
        _close_investigation()
        st.session_state["turns"] = []

    repo = repo_input.strip() or None
    if repo and mode != "project":
        try:
            resolve_repo(settings, repo)
        except GitHubError as exc:
            st.error(str(exc))
            return

    current = st.session_state.get("investigation")
    if current and current["key"] != (str(project), repo, mode):
        st.info("Settings changed: the next question starts a new conversation.")

    for turn in st.session_state.get("turns", []):
        with st.chat_message("user"):
            st.markdown(turn.question)
        with st.chat_message("assistant"):
            render_investigation(turn)

    question = st.chat_input("Ask about the project or its GitHub issues...")
    if not question:
        return
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        try:
            investigator = get_investigator(settings, project, repo, mode)  # started on first use
            with st.spinner("Investigating..."):
                result = investigator.ask(question)
        except (LLMError, OrchestrationConfigError, GitHubError) as exc:
            st.error(str(exc))
            return
        render_investigation(result)
    st.session_state.setdefault("turns", []).append(result)


def main() -> None:
    try:
        settings = get_settings()
    except ValueError as exc:
        st.error(f"Invalid configuration: {exc}")
        st.stop()

    project, top_k, file_types = sidebar(settings)
    st.title("AI Developer Assistant")
    quick, investigate = st.tabs(["Quick answer", "Investigate"])
    with quick:
        quick_answer_tab(settings, project, top_k, file_types)
    with investigate:
        investigate_tab(settings.model_copy(update={"project_root": project}), project)


main()
