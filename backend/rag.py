"""
RAG CLI — query the BNS Qdrant collection.

Usage
-----
    python rag.py                                      # interactive loop
    python rag.py "What is the punishment for murder?" # single question
    python rag.py "..." --top-k 20                     # change result count
    python rag.py "..." --file-id file_abc123          # restrict to one file
    python rag.py "..." --no-parent                    # child text only, no parent expansion
    python rag.py "..." --no-expand                    # skip heading-based sibling expansion
    python rag.py "..." --no-stream                    # print full answer at once
    python rag.py "..." --show-context                 # print retrieved context blocks

Environment variables (read from .env)
---------------------------------------
    QDRANT_LOCAL_PATH   - path to local Qdrant storage dir (e.g. ./qdrant_local)
                          if set, runs Qdrant embedded — no server needed.
                          if unset, falls back to QDRANT_URL.
    QDRANT_URL          - e.g. http://localhost:6333  (used when no QDRANT_LOCAL_PATH)
    QDRANT_API_KEY      - API key for remote Qdrant (leave empty for local)
    QDRANT_COLLECTION   - collection name, default parent_child_chunks
    OPENAI_API_KEY      - OpenAI key (used for embeddings only)
    GROQ_API_KEY        - Groq key for open-weight LLM generation (Llama 3.3 70B)
    EMBEDDING_MODEL     - default text-embedding-3-large
    LLM_MODEL           - chat model, default llama-3.3-70b-versatile (via Groq)
    RAG_TOP_K           - number of child hits to retrieve, default 10
"""

from __future__ import annotations

import argparse
import os
import sys
import textwrap
import warnings
from contextlib import nullcontext
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, urlunparse

# Suppress qdrant_client's "Api key used with insecure connection" warning
# (expected on internal HTTP Qdrant endpoints that use API key auth without TLS)
warnings.filterwarnings("ignore", message="Api key is used with an insecure connection")

# ── Force UTF-8 on Windows console ──────────────────────────────────────────
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except OSError:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except OSError:
        pass

# ── Load .env before anything else ──────────────────────────────────────────
from dotenv import load_dotenv

load_dotenv()

from openai import OpenAI
from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

def _is_interactive_stream() -> bool:
    try:
        return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
    except OSError:
        return False


console = Console(quiet=not _is_interactive_stream())


def _safe_status(message: str, enabled: bool = True):
    """
    Rich live status spinners can fail in non-interactive stdout (e.g. FastAPI
    on Windows) with OSError: [Errno 22] Invalid argument.
    Fall back to a no-op context manager when not attached to a TTY.
    """
    if not enabled:
        return nullcontext()
    try:
        if hasattr(sys.stdout, "isatty") and sys.stdout.isatty():
            return console.status(message, spinner="dots")
    except OSError:
        pass
    return nullcontext()

# ── Config ───────────────────────────────────────────────────────────────────
QDRANT_LOCAL_PATH = os.getenv("QDRANT_LOCAL_PATH", "")          # embedded mode if set
QDRANT_URL        = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_API_KEY    = os.getenv("QDRANT_API_KEY", "")
COLLECTION        = os.getenv("QDRANT_COLLECTION", "parent_child_chunks")

GROQ_API_KEY    = os.getenv("GROQ_API_KEY", "")                 # open-weight LLM
# Free local embedding model — must match what was used during ingest_chunks.py.
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "BAAI/bge-base-en-v1.5")
CHAT_MODEL = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")
DEFAULT_TOP_K = int(os.getenv("RAG_TOP_K", "8"))
# Groq free tier: 12,000 TPM. Legal text is ~1.5 chars/token (very short words).
# Reserve ~1500 tokens for system prompt + question + answer → ~10,500 tokens for context.
# 10,500 * 1.5 ≈ 15,750 chars — use 14,000 to be safe.
MAX_CONTEXT_CHARS = int(os.getenv("RAG_MAX_CONTEXT_CHARS", "14000"))
# When siblings don't fit full-text, compact each to this many chars (keeps heading + gist)
SIBLING_COMPACT_CHARS = int(os.getenv("RAG_SIBLING_COMPACT_CHARS", "300"))

# ── Expansion quality controls ─────────────────────────────────────────────────
# Discard hits below this score before they can pollute context or trigger expansion
RAG_MIN_HIT_SCORE            = float(os.getenv("RAG_MIN_HIT_SCORE",            "0.35"))
# A hit must reach this score to contribute its full heading_text to precise expansion
RAG_EXPANSION_SPECIFIC_SCORE = float(os.getenv("RAG_EXPANSION_SPECIFIC_SCORE",  "0.45"))
# A top-level heading must appear in at least this many hits to qualify for broad expansion
RAG_EXPANSION_BROAD_MIN_HITS = int(os.getenv("RAG_EXPANSION_BROAD_MIN_HITS",    "2"))
# Hard cap: no more than this many distinct heading expansions per query
RAG_MAX_EXPANSION_HEADINGS   = int(os.getenv("RAG_MAX_EXPANSION_HEADINGS",      "5"))
# Also search parent chunks directly by vector (free second query, improves general questions)
RAG_SEARCH_PARENTS_DIRECT = os.getenv("RAG_SEARCH_PARENTS_DIRECT", "true").lower() in ("1", "true", "yes")
# Decompose broad queries into sub-queries before searching (opt-in; costs extra LLM call)
RAG_QUERY_DECOMPOSE = os.getenv("RAG_QUERY_DECOMPOSE", "false").lower() in ("1", "true", "yes")
RAG_DECOMPOSE_N     = int(os.getenv("RAG_DECOMPOSE_N", "3"))
# Reconstruct parent text from children if stored text is near the 8000-char storage cap
PARENT_TRUNCATION_THRESHOLD = int(os.getenv("RAG_PARENT_TRUNCATION_THRESHOLD", "7500"))

SYSTEM_PROMPT = """\
You are a precise document-analysis assistant. \
Answer the user's question using ONLY the information in the provided context blocks.

Each context block is labelled [Source N] and contains:
  - Section: the heading path from the document 
  - File, Page, Score metadata
  - The full or summarised text of that section

Formatting rules — follow these strictly:
1. **Use document headings as your answer headings.**
   - If the answer covers multiple named sections/schemes/topics, use `##` Markdown \
headers that match the section names from the sources.
   - For sub-points within a section use `###`.
   - Never invent heading names — use the exact names from the 'Section:' field.
2. **For listing items** (schemes, criteria, documents, etc.):
   - Use a numbered list.
   - Bold each item name on its own line, then indent details as bullet points.
3. **Cite every claim** inline with the source label, e.g. [Source 1].
4. If multiple sources cover the same topic, cite all of them.
5. If the answer is not in the context, say so clearly — do not guess.
6. Be thorough — extract and present every relevant detail from the context.
7. NEVER use the phrase "unless the context otherwise requires". Omit it completely from your answers.
8. Do not use the generic phrase "In this Sanhita". Always use "In the Bharatiya Nyaya Sanhita (BNS)".
"""


# ── Helpers ──────────────────────────────────────────────────────────────────

def _normalize_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.path and parsed.path not in ("", "/"):
        url = urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))
    return url.rstrip("/")


# ── Clients ───────────────────────────────────────────────────────────────────

# Embedding model is loaded once at startup (first call) and reused.
_embed_model: Optional[Any] = None


def get_qdrant_client() -> QdrantClient:
    if QDRANT_LOCAL_PATH:
        # Embedded mode — no server or Docker needed. Data stored at QDRANT_LOCAL_PATH.
        return QdrantClient(path=QDRANT_LOCAL_PATH)
    return QdrantClient(
        url=_normalize_url(QDRANT_URL),
        api_key=QDRANT_API_KEY or None,
        check_compatibility=False,
    )


def get_embedding_client() -> Any:
    """Local open-weight embedding model — no API key required."""
    global _embed_model
    if _embed_model is None:
        # Lazy import keeps FastAPI startup memory lower on free hosts.
        from sentence_transformers import SentenceTransformer
        _embed_model = SentenceTransformer(EMBEDDING_MODEL)
    return _embed_model


def get_llm_client() -> OpenAI:
    """Groq client for open-weight Llama generation."""
    if not GROQ_API_KEY:
        console.print("[bold red]GROQ_API_KEY not set. Get a free key at https://console.groq.com[/bold red]")
        sys.exit(1)
    return OpenAI(api_key=GROQ_API_KEY, base_url="https://api.groq.com/openai/v1")


# Keep for backwards compatibility
def get_openai_client() -> Any:
    return get_embedding_client()


# ── Retrieval ─────────────────────────────────────────────────────────────────

def embed_query(model: Any, query: str) -> List[float]:
    """Embed the user's question using the local embedding model."""
    return model.encode(query, normalize_embeddings=True).tolist()


def decompose_query(client: OpenAI, question: str, n: int = 3) -> List[str]:
    """Ask the LLM to split a broad question into n specific search sub-queries."""
    response = client.chat.completions.create(
        model=CHAT_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    f"Generate {n} specific search sub-queries that together cover all aspects "
                    "of the user's question. Return only the sub-queries, one per line, "
                    "no numbering or extra text."
                ),
            },
            {"role": "user", "content": question},
        ],
        temperature=0,
        max_tokens=150,
    )
    lines = [
        l.strip()
        for l in (response.choices[0].message.content or "").splitlines()
        if l.strip()
    ]
    return lines[:n]


def merge_hits(hits_lists: List[List]) -> List:
    """Merge hits from multiple query vectors; keep the highest score per chunk."""
    best: dict = {}
    for hits in hits_lists:
        for hit in hits:
            cid = hit.payload.get("chunk_id", str(hit.id))
            if cid not in best or hit.score > best[cid].score:
                best[cid] = hit
    return sorted(best.values(), key=lambda h: -h.score)


def search_children(
    qdrant: QdrantClient,
    query_vector: List[float],
    top_k: int = 10,
    file_id: Optional[str] = None,
) -> List[dict]:
    """
    Vector-search Qdrant for child chunks only.
    Parent/master points have zero vectors and are excluded.
    Uses query_points() (qdrant-client >= 1.7).
    """
    must = [
        FieldCondition(key="is_delete", match=MatchValue(value=False)),
        FieldCondition(key="is_active", match=MatchValue(value=True)),
        FieldCondition(
            key="chunk_type",
            match=MatchAny(any=["child", "standalone"]),
        ),
    ]
    if file_id:
        must.append(FieldCondition(key="file_id", match=MatchValue(value=file_id)))

    response = qdrant.query_points(
        collection_name=COLLECTION,
        query=query_vector,
        limit=top_k,
        with_payload=True,
        query_filter=Filter(must=must),
    )
    return response.points


def search_parents_direct(
    qdrant: QdrantClient,
    query_vector: List[float],
    top_k: int = 15,
    file_id: Optional[str] = None,
) -> List:
    """
    Vector-search Qdrant for parent chunks directly.
    Parent chunks hold aggregated section text and match general queries better
    than individual child chunks.
    """
    must = [
        FieldCondition(key="is_delete", match=MatchValue(value=False)),
        FieldCondition(key="is_active", match=MatchValue(value=True)),
        FieldCondition(key="chunk_type", match=MatchValue(value="parent")),
    ]
    if file_id:
        must.append(FieldCondition(key="file_id", match=MatchValue(value=file_id)))

    response = qdrant.query_points(
        collection_name=COLLECTION,
        query=query_vector,
        limit=top_k,
        with_payload=True,
        query_filter=Filter(must=must),
    )
    return response.points


def fetch_parents(
    qdrant: QdrantClient,
    parent_ids: List[str],
) -> Dict[str, str]:
    """Batch-fetch parent chunk texts by their IDs."""
    if not parent_ids:
        return {}
    try:
        points = qdrant.retrieve(
            collection_name=COLLECTION,
            ids=parent_ids,
            with_payload=True,
        )
        return {str(p.id): p.payload.get("original_text", "") for p in points}
    except Exception as exc:
        console.print(f"[yellow]  Parent fetch failed: {exc}[/yellow]")
        return {}


def expand_truncated_parents(
    qdrant: QdrantClient,
    parent_map: Dict[str, str],
) -> None:
    """
    For any parent whose stored text is near the 8000-char cap, scroll its child
    chunks and replace the truncated text with the full ordered concatenation.
    Mutates parent_map in-place; no return value.
    """
    truncated = [pid for pid, text in parent_map.items()
                 if len(text) >= PARENT_TRUNCATION_THRESHOLD]
    if not truncated:
        return
    console.print(f"[dim]  ✦ Reconstructing {len(truncated)} truncated parent(s) from children…[/dim]")
    for parent_id in truncated:
        try:
            results, _ = qdrant.scroll(
                collection_name=COLLECTION,
                scroll_filter=Filter(must=[
                    FieldCondition(key="parent_chunk_id", match=MatchValue(value=parent_id)),
                    FieldCondition(key="chunk_type",      match=MatchValue(value="child")),
                    FieldCondition(key="is_delete",       match=MatchValue(value=False)),
                    FieldCondition(key="is_active",       match=MatchValue(value=True)),
                ]),
                limit=200,
                with_payload=True,
            )
            if results:
                ordered = sorted(results, key=lambda p: p.payload.get("chunk_index", 0))
                parent_map[parent_id] = "\n\n".join(
                    p.payload.get("original_text", "") for p in ordered
                )
        except Exception as exc:
            console.print(f"[yellow]  Child reconstruction failed for {parent_id}: {exc}[/yellow]")


def fetch_siblings_by_heading(
    qdrant: QdrantClient,
    specific_heading_paths: set,
    broad_headings: List[str],
    already_seen_parent_ids: set,
    file_id: Optional[str] = None,
) -> List[dict]:
    """
    Scroll Qdrant for parent chunks in two passes:

    Scroll A — precise: matches the full heading_text path (e.g. "A > B > C").
      Used for high-confidence hits where we want only the specific sub-section.

    Scroll B — broad: matches the top-level parent_heading (e.g. "A").
      Only runs for headings that appeared in multiple hits (strong topic signal).

    Both scrolls share already_seen_parent_ids so there is no duplication.
    """
    if not specific_heading_paths and not broad_headings:
        return []

    sibling_blocks: List[dict] = []
    base_must = [
        FieldCondition(key="is_delete", match=MatchValue(value=False)),
        FieldCondition(key="is_active", match=MatchValue(value=True)),
        FieldCondition(key="chunk_type", match=MatchValue(value="parent")),
    ]
    if file_id:
        base_must.append(FieldCondition(key="file_id", match=MatchValue(value=file_id)))

    def _scroll_and_collect(extra_condition: FieldCondition, block_type: str) -> None:
        must = base_must + [extra_condition]
        offset = None
        while True:
            try:
                results, offset = qdrant.scroll(
                    collection_name=COLLECTION,
                    scroll_filter=Filter(must=must),
                    limit=100,
                    offset=offset,
                    with_payload=True,
                )
            except Exception as exc:
                console.print(f"[yellow]  Sibling scroll failed: {exc}[/yellow]")
                break

            for point in results:
                pid = str(point.id)
                if pid in already_seen_parent_ids:
                    continue
                already_seen_parent_ids.add(pid)
                payload = point.payload
                sibling_blocks.append(
                    {
                        "context_text": payload.get("original_text", ""),
                        "child_text": "",
                        "heading": payload.get("heading_text", "") or "",
                        "page": payload.get("page_number", "?"),
                        "score": 0.0,
                        "filename": payload.get("filename", ""),
                        "type": block_type,
                    }
                )

            if offset is None:
                break

    # Scroll A — precise expansion by full heading path
    if specific_heading_paths:
        _scroll_and_collect(
            FieldCondition(key="heading_text", match=MatchAny(any=list(specific_heading_paths))),
            "heading_specific",
        )

    # Scroll B — broad expansion by top-level heading (guarded by hit-frequency check upstream)
    if broad_headings:
        _scroll_and_collect(
            FieldCondition(key="parent_heading", match=MatchAny(any=broad_headings)),
            "heading_sibling",
        )

    # Pass C — fetch children by parent_heading for non-truncated full content.
    # Parent chunks are stored with an 8000-char cap; children carry the actual text.
    if broad_headings:
        child_must_base = [
            FieldCondition(key="is_delete",  match=MatchValue(value=False)),
            FieldCondition(key="is_active",  match=MatchValue(value=True)),
            FieldCondition(key="chunk_type", match=MatchAny(any=["child", "standalone"])),
        ]
        if file_id:
            child_must_base.append(FieldCondition(key="file_id", match=MatchValue(value=file_id)))

        seen_child_ids: set = set()
        for heading in broad_headings:
            offset = None
            while True:
                try:
                    results, offset = qdrant.scroll(
                        collection_name=COLLECTION,
                        scroll_filter=Filter(must=child_must_base + [
                            FieldCondition(key="parent_heading",
                                           match=MatchValue(value=heading)),
                        ]),
                        limit=100,
                        offset=offset,
                        with_payload=True,
                    )
                except Exception as exc:
                    console.print(f"[yellow]  Child expansion scroll failed: {exc}[/yellow]")
                    break

                for point in sorted(results, key=lambda p: p.payload.get("chunk_index", 0)):
                    cid = point.payload.get("chunk_id", str(point.id))
                    if cid in seen_child_ids or str(point.id) in already_seen_parent_ids:
                        continue
                    seen_child_ids.add(cid)
                    payload = point.payload
                    sibling_blocks.append({
                        "context_text": payload.get("original_text", ""),
                        "child_text":   payload.get("original_text", ""),
                        "heading":      payload.get("heading_text", "") or "",
                        "page":         payload.get("page_number", "?"),
                        "score":        0.0,
                        "filename":     payload.get("filename", ""),
                        "type":         "heading_child",
                    })

                if offset is None:
                    break

    return sibling_blocks


# ── Context builder ───────────────────────────────────────────────────────────

def build_context_blocks(
    hits: list,
    parent_map: Dict[str, str],
    use_parent: bool,
) -> tuple[List[dict], set, set, Dict[str, List[float]]]:
    """
    Build deduplicated context blocks from the initial vector-search hits.

    Returns:
        blocks                - list of context dicts
        seen_parent_ids       - set of parent IDs already included (for sibling dedup)
        specific_heading_paths - full heading_text values from high-confidence hits
        broad_heading_scores  - top-level parent_heading → list of hit scores
    """
    seen_parent_ids: set = set()
    seen_child_ids: set = set()
    specific_heading_paths: set = set()
    broad_heading_scores: Dict[str, List[float]] = {}
    blocks: List[dict] = []

    for hit in hits:
        payload = hit.payload
        chunk_type = payload.get("chunk_type", "standalone")
        parent_id = payload.get("parent_chunk_id", "")
        child_text = payload.get("original_text", "")
        heading = payload.get("heading_text", "") or ""
        page = payload.get("page_number", "?")
        score = round(hit.score, 4)
        filename = payload.get("filename", "")

        # Collect heading data for smart sibling expansion
        full_heading = payload.get("heading_text", "") or ""
        top_heading  = payload.get("parent_heading", "") or ""
        if full_heading and score >= RAG_EXPANSION_SPECIFIC_SCORE:
            specific_heading_paths.add(full_heading)
        if top_heading:
            broad_heading_scores.setdefault(top_heading, []).append(score)

        if use_parent and chunk_type == "child" and parent_id and parent_id in parent_map:
            # Deduplicate: show each parent section only once
            if parent_id in seen_parent_ids:
                continue
            seen_parent_ids.add(parent_id)
            blocks.append(
                {
                    "context_text": parent_map[parent_id],
                    "child_text": child_text,
                    "heading": heading,
                    "page": page,
                    "score": score,
                    "filename": filename,
                    "type": "parent_expanded",
                }
            )
        else:
            # No parent expansion — use child text directly, deduplicate by chunk_id
            chunk_id = payload.get("chunk_id", "")
            if chunk_id in seen_child_ids:
                continue
            seen_child_ids.add(chunk_id)
            blocks.append(
                {
                    "context_text": child_text,
                    "child_text": child_text,
                    "heading": heading,
                    "page": page,
                    "score": score,
                    "filename": filename,
                    "type": "child_only",
                }
            )

    return blocks, seen_parent_ids, specific_heading_paths, broad_heading_scores


def format_context_for_llm(blocks: List[dict]) -> str:
    """
    Format context blocks for the LLM prompt.

    Heading path is rendered explicitly so the LLM can use it as a Markdown
    header in its answer.  Compacted blocks are flagged so the LLM knows the
    text is a summary, not the complete section.
    """
    parts = []
    for i, blk in enumerate(blocks, 1):
        heading = blk.get("heading", "") or ""
        # Replace docling separator " > " with a cleaner arrow for readability
        heading_display = heading.replace(" > ", " → ").strip()

        meta_parts = []
        if blk["filename"]:
            meta_parts.append(f"File: {blk['filename']}")
        meta_parts.append(f"Page {blk['page']}")
        if blk["score"] > 0:
            meta_parts.append(f"Score {blk['score']}")
        meta = "  |  ".join(meta_parts)

        is_compact = blk.get("compacted", False)
        text = blk["context_text"].strip()

        block_str = f"[Source {i}]"
        if heading_display:
            block_str += f"\nSection: {heading_display}"
        block_str += f"\n{meta}"
        if is_compact:
            block_str += "  |  [summarised — full section available]"
        block_str += f"\n\n{text}"
        parts.append(block_str)

    return "\n\n" + ("-" * 60 + "\n\n").join(parts)


def trim_blocks_to_budget(
    blocks: List[dict],
    max_chars: int = MAX_CONTEXT_CHARS,
    sibling_compact_chars: int = SIBLING_COMPACT_CHARS,
) -> tuple[List[dict], int]:
    """
    Fit blocks within the character budget.

    Strategy:
    1. Sort direct hits (score > 0) by score DESC; fill from the top until budget is gone.
       Each direct block is hard-capped at max_chars // 3 so one large block can't consume
       the entire budget (important when truncated parents are reconstructed from children).
    2. Fill remaining budget with siblings compacted to sibling_compact_chars each.

    Returns (final_blocks, n_compacted).
    """
    direct = sorted([b for b in blocks if b["score"] > 0], key=lambda b: -b["score"])
    siblings = sorted(
        [b for b in blocks if b["score"] == 0],
        key=lambda b: b["page"] if isinstance(b["page"], int) else 0,
    )

    # Per-block cap for direct hits so reconstructed parents don't monopolise context
    per_block_cap = max(sibling_compact_chars, max_chars // max(1, len(direct)))

    n_compacted = 0
    final_direct: List[dict] = []
    used = 0
    for blk in direct:
        text = blk["context_text"]
        if len(text) > per_block_cap:
            text = text[:per_block_cap].rstrip() + " …"
            blk = {**blk, "context_text": text, "compacted": True}
            n_compacted += 1
        if used + len(text) > max_chars:
            break
        final_direct.append(blk)
        used += len(text)

    sibling_budget = max_chars - used
    final_siblings: List[dict] = []

    if sibling_budget > 0:
        used_sib = 0
        for blk in siblings:
            text = blk["context_text"]
            if len(text) > sibling_compact_chars:
                # Compact: keep first N chars + ellipsis
                text = text[:sibling_compact_chars].rstrip() + " …"
                blk = {**blk, "context_text": text, "compacted": True}
                n_compacted += 1
            blk_chars = len(text)
            if used_sib + blk_chars > sibling_budget:
                break  # no more room even for compacted text
            final_siblings.append(blk)
            used_sib += blk_chars

    return final_direct + final_siblings, n_compacted


# ────────────────────────────────────────────────── LLM call ─────────────────────────────────────────────────────────────

def ask_llm(
    openai_client: OpenAI,
    question: str,
    context: str,
    stream: bool = True,
) -> str:
    """Send question + context to the LLM and return (or stream) the answer."""
    user_message = (
        f"Context:\n{context}\n\n"
        f"---\n\nQuestion: {question}"
    )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]

    if stream:
        answer_parts: List[str] = []
        with _safe_status("Thinking..."):
            response_stream = openai_client.chat.completions.create(
                model=CHAT_MODEL,
                messages=messages,
                temperature=0.1,
                stream=True,
            )

        # Print streaming tokens
        console.print()
        for chunk in response_stream:
            delta = chunk.choices[0].delta.content or ""
            print(delta, end="", flush=True)
            answer_parts.append(delta)
        print()  # newline after streaming
        return "".join(answer_parts)
    else:
        with _safe_status("Thinking..."):
            response = openai_client.chat.completions.create(
                model=CHAT_MODEL,
                messages=messages,
                temperature=0.1,
            )
        return response.choices[0].message.content or ""


# ────────────────────────────────────────────────── Pretty printing ───────────────────────────────────────────────────────────

def print_sources(blocks: List[dict]) -> None:
    console.print()
    console.print(Rule("Sources", style="dim"))
    for i, blk in enumerate(blocks, 1):
        heading = blk["heading"] or "(no heading)"
        filename = blk["filename"] or ""
        info = Text()
        info.append(f"[Source {i}] ", style="bold cyan")
        info.append(f"p.{blk['page']}  score={blk['score']}  ", style="dim")
        if filename:
            info.append(f"📄 {filename}  ", style="dim")
        info.append(f"§ {heading}", style="italic")
        console.print(info)


def print_context_blocks(blocks: List[dict]) -> None:
    console.print()
    console.print(Rule("Retrieved Context", style="blue dim"))
    for i, blk in enumerate(blocks, 1):
        snippet = textwrap.shorten(blk["context_text"], width=300, placeholder=" …")
        console.print(
            Panel(
                snippet,
                title=f"[cyan][Source {i}][/cyan]  p.{blk['page']}  {blk['heading'] or ''}",
                subtitle=f"score={blk['score']}  type={blk['type']}",
                expand=False,
            )
        )


# ────────────────────────────────────────────────── Main RAG flow ─────────────────────────────────────────────────────────────

def rag_query(
    question: str,
    top_k: int = DEFAULT_TOP_K,
    file_id: Optional[str] = None,
    use_parent: bool = True,
    expand_by_heading: bool = True,
    stream: bool = True,
    show_context: bool = False,
    use_decompose: bool = RAG_QUERY_DECOMPOSE,
    verbose: bool = True,
) -> str:
    """
    End-to-end RAG:
      embed → (decompose) → search children → direct parent search →
      fetch parents → expand by heading → LLM
    """

    embed_client = get_embedding_client()
    llm_client   = get_llm_client()
    qdrant = get_qdrant_client()

    # 1. Embed the question
    with _safe_status("Embedding question...", enabled=verbose):
        query_vector = embed_query(embed_client, question)

    # 2. Search child chunks (multi-vector when decompose is on)
    if use_decompose:
        with _safe_status(
            f"Decomposing query into {RAG_DECOMPOSE_N} sub-queries...",
            enabled=verbose,
        ):
            sub_queries = decompose_query(llm_client, question, n=RAG_DECOMPOSE_N)
        if verbose:
            console.print(f"[dim]  Sub-queries: {' | '.join(sub_queries)}[/dim]")
        all_vectors = [query_vector]
        for sq in sub_queries:
            with _safe_status(f"Embedding: {sq[:60]}...", enabled=verbose):
                all_vectors.append(embed_query(embed_client, sq))
        with _safe_status(f"Searching {len(all_vectors)} query vectors...", enabled=verbose):
            hits = merge_hits([
                search_children(qdrant, vec, top_k=top_k, file_id=file_id)
                for vec in all_vectors
            ])
    else:
        with _safe_status(f"Searching top-{top_k} chunks...", enabled=verbose):
            hits = search_children(qdrant, query_vector, top_k=top_k, file_id=file_id)

    if not hits:
        if verbose:
            console.print("[bold red]No relevant chunks found in Qdrant.[/bold red]")
        return ""

    # Discard low-quality hits before they pollute context or trigger false expansion
    original_hit_count = len(hits)
    hits = [h for h in hits if h.score >= RAG_MIN_HIT_SCORE]
    if not hits:
        if verbose:
            console.print(
                f"[bold red]All {original_hit_count} hit(s) scored below "
                f"{RAG_MIN_HIT_SCORE} — no relevant content found.[/bold red]"
            )
        return ""
    if original_hit_count != len(hits):
        if verbose:
            console.print(
                f"[dim]  ✓ {len(hits)}/{original_hit_count} chunk(s) after score filter "
                f"(≥ {RAG_MIN_HIT_SCORE})[/dim]"
            )
    else:
        if verbose:
            console.print(f"[dim]  ✓ {len(hits)} child chunk(s) retrieved[/dim]")

    # 3. Fetch parent texts for direct hits
    parent_map: Dict[str, str] = {}
    if use_parent:
        parent_ids = list(
            {
                h.payload.get("parent_chunk_id", "")
                for h in hits
                if h.payload.get("chunk_type") == "child"
                and h.payload.get("parent_chunk_id", "")
            }
        )
        if parent_ids:
            with _safe_status(f"Fetching {len(parent_ids)} parent section(s)...", enabled=verbose):
                parent_map = fetch_parents(qdrant, parent_ids)
            if verbose:
                console.print(f"[dim]  ✓ {len(parent_map)} parent section(s) fetched[/dim]")
        if parent_map:
            expand_truncated_parents(qdrant, parent_map)

    # 3.5 Direct parent search — section-level chunks match general queries better than children
    parent_direct_blocks: List[dict] = []
    if use_parent and RAG_SEARCH_PARENTS_DIRECT:
        with _safe_status("Searching parent sections directly...", enabled=verbose):
            p_hits = search_parents_direct(
                qdrant, query_vector, top_k=max(10, top_k // 3), file_id=file_id
            )
        p_hits = [h for h in p_hits if h.score >= RAG_MIN_HIT_SCORE]
        if p_hits and verbose:
            console.print(f"[dim]  ✓ {len(p_hits)} parent section(s) matched directly[/dim]")
        # Store for merging after build_context_blocks (dedup happens there via seen_parent_ids)
        for hit in p_hits:
            payload = hit.payload
            parent_direct_blocks.append({
                "context_text": payload.get("original_text", ""),
                "child_text": "",
                "heading": payload.get("heading_text", "") or "",
                "page": payload.get("page_number", "?"),
                "score": round(hit.score, 4),
                "filename": payload.get("filename", ""),
                "type": "direct_parent_hit",
                "_chunk_id": payload.get("chunk_id", str(hit.id)),
                "_point_id": str(hit.id),
            })

    # 4. Build primary context blocks
    blocks, seen_parent_ids, specific_heading_paths, broad_heading_scores = build_context_blocks(
        hits, parent_map, use_parent=use_parent
    )

    # Merge direct parent hits, deduplicating against sections already in context
    for blk in parent_direct_blocks:
        if blk["_chunk_id"] in seen_parent_ids or blk["_point_id"] in seen_parent_ids:
            continue
        seen_parent_ids.add(blk["_chunk_id"])
        seen_parent_ids.add(blk["_point_id"])
        blocks.append({k: v for k, v in blk.items() if not k.startswith("_")})

    if not blocks:
        if verbose:
            console.print("[yellow]⚠  No context blocks could be built.[/yellow]")
        return ""

    # 5. Heading-based sibling expansion (precision-controlled)
    #    Scroll A — precise: expands only the specific sub-section paths from high-score hits.
    #    Scroll B — broad: expands top-level headings only when 2+ hits confirm the topic.
    if expand_by_heading and use_parent:
        # Qualify broad headings: require multiple hits OR a high avg score
        qualified_broad: List[str] = [
            h for h, scores in broad_heading_scores.items()
            if len(scores) >= RAG_EXPANSION_BROAD_MIN_HITS
            or (sum(scores) / len(scores)) >= RAG_EXPANSION_SPECIFIC_SCORE
        ]

        # Cap total expansions; specific paths take priority over broad headings
        capped_specific = set(list(specific_heading_paths)[:RAG_MAX_EXPANSION_HEADINGS])
        remaining_slots = RAG_MAX_EXPANSION_HEADINGS - len(capped_specific)
        capped_broad = qualified_broad[:max(0, remaining_slots)]

        if capped_specific or capped_broad:
            with _safe_status(
                f"Expanding {len(capped_specific)} specific + {len(capped_broad)} broad heading(s)...",
                enabled=verbose,
            ):
                sibling_blocks = fetch_siblings_by_heading(
                    qdrant,
                    specific_heading_paths=capped_specific,
                    broad_headings=capped_broad,
                    already_seen_parent_ids=seen_parent_ids,
                    file_id=file_id,
                )
            if sibling_blocks:
                sibling_blocks.sort(key=lambda b: b["page"] if isinstance(b["page"], int) else 0)
                blocks.extend(sibling_blocks)
                if verbose:
                    console.print(
                        f"[dim]  ✓ {len(sibling_blocks)} sibling section(s) added "
                        f"({len(capped_specific)} specific + {len(capped_broad)} broad heading(s))[/dim]"
                    )

    if show_context and verbose:
        print_context_blocks(blocks)

    # Trim to fit LLM context budget (prioritise direct hits over siblings)
    original_count = len(blocks)
    blocks, n_compacted = trim_blocks_to_budget(blocks)
    if n_compacted and verbose:
        console.print(
            f"[dim]  ✓ {n_compacted} sibling block(s) compacted to fit context budget "
            f"({original_count} blocks total → all included)[/dim]"
        )

    context_str = format_context_for_llm(blocks)

    # 6. Call LLM
    if verbose:
        console.print()
        console.print(Rule("Answer", style="green"))
    answer = ask_llm(llm_client, question, context_str, stream=stream)

    if not stream and verbose:
        console.print(Markdown(answer))

    # 7. Show sources
    if verbose:
        print_sources(blocks)

    return answer


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="RAG CLI — query the Qdrant parent-child chunks collection",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(
            """\
            Examples:
              python rag.py "What is the loan limit?"
              python rag.py --top-k 20 "Explain eligibility criteria"
              python rag.py --no-parent --show-context "What fees apply?"
              python rag.py --file-id file_abc123 "Summarise section 3"
              python rag.py          # interactive loop (Ctrl-C to exit)
            """
        ),
    )
    parser.add_argument(
        "question",
        nargs="?",
        default=None,
        help="Question to ask (omit for interactive mode)",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=DEFAULT_TOP_K,
        dest="top_k",
        help=f"Number of child chunks to retrieve (default: {DEFAULT_TOP_K})",
    )
    parser.add_argument(
        "--file-id",
        default=None,
        dest="file_id",
        help="Restrict search to a specific file_id in Qdrant",
    )
    parser.add_argument(
        "--no-parent",
        action="store_true",
        dest="no_parent",
        help="Skip parent expansion — use child text only",
    )
    parser.add_argument(
        "--no-expand",
        action="store_true",
        dest="no_expand",
        help="Skip heading-based sibling expansion (faster, may miss parts of large topics)",
    )
    parser.add_argument(
        "--no-stream",
        action="store_true",
        dest="no_stream",
        help="Print full answer at once instead of streaming",
    )
    parser.add_argument(
        "--show-context",
        action="store_true",
        dest="show_context",
        help="Print retrieved context blocks before the answer",
    )
    parser.add_argument(
        "--decompose",
        action="store_true",
        dest="decompose",
        help="Decompose query into sub-queries for better recall on broad/general questions",
    )
    return parser.parse_args()


def interactive_loop(args: argparse.Namespace) -> None:
    console.print(
        Panel(
            "[bold green]RAG CLI[/bold green]  —  Qdrant × OpenAI\n"
            f"[dim]Collection:[/dim] [cyan]{COLLECTION}[/cyan]   "
            f"[dim]Embedding:[/dim] [cyan]{EMBEDDING_MODEL}[/cyan]   "
            f"[dim]Chat model:[/dim] [cyan]{CHAT_MODEL}[/cyan]\n\n"
            "Type your question and press Enter. "
            "[dim]Ctrl-C to exit.[/dim]",
            title="Welcome",
            expand=False,
        )
    )

    while True:
        try:
            console.print()
            question = console.input("[bold cyan]Question:[/bold cyan] ").strip()
            if not question:
                continue
            console.print(Rule(style="dim"))
            rag_query(
                question=question,
                top_k=args.top_k,
                file_id=args.file_id,
                use_parent=not args.no_parent,
                expand_by_heading=not args.no_expand,
                stream=not args.no_stream,
                show_context=args.show_context,
                use_decompose=args.decompose or RAG_QUERY_DECOMPOSE,
            )
        except KeyboardInterrupt:
            console.print("\n[dim]Bye![/dim]")
            break


def main() -> None:
    if not os.getenv("GROQ_API_KEY"):
        console.print("[bold red]Missing GROQ_API_KEY. Get a free key at https://console.groq.com[/bold red]")
        sys.exit(1)
    if not os.getenv("QDRANT_LOCAL_PATH") and not os.getenv("QDRANT_URL"):
        console.print("[bold red]Set QDRANT_LOCAL_PATH (embedded) or QDRANT_URL (server).[/bold red]")
        sys.exit(1)

    args = parse_args()

    if args.question:
        # Single-shot mode
        console.print(
            Panel(
                f"[bold]{args.question}[/bold]",
                title="[cyan]Query[/cyan]",
                expand=False,
            )   
        )
        rag_query(
            question=args.question,
            top_k=args.top_k,
            file_id=args.file_id,
            use_parent=not args.no_parent,
            expand_by_heading=not args.no_expand,
            stream=not args.no_stream,
            show_context=args.show_context,
            use_decompose=args.decompose or RAG_QUERY_DECOMPOSE,
        )
    else:
        # Interactive loop
        interactive_loop(args)


if __name__ == "__main__":
    main()


                                                                                                                                                                                            


