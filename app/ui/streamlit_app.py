"""Streamlit UI for asking questions about an indexed repository.

Run with:  uv run streamlit run app/ui/streamlit_app.py

The UI only gathers input and renders results; indexing, retrieval and answer
generation all go through the same functions the CLI uses.
"""

from pathlib import Path

import streamlit as st

from app.config import Settings, get_settings
from app.llm.base import LLMError
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


def main() -> None:
    try:
        settings = get_settings()
    except ValueError as exc:
        st.error(f"Invalid configuration: {exc}")
        st.stop()

    project, top_k, file_types = sidebar(settings)

    st.title("AI Developer Assistant")
    st.caption("Ask questions about the indexed project. Answers cite the project files they rely on.")
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


main()
