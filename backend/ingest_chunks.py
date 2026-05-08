"""
Ingest chunks (from new_docling.py) into Qdrant.

Completely standalone — does not import anything from the project.
Builds a parent-child structure compatible with rag.py.

Usage:
    python ingest_chunks.py --json chunks_s1.json
    python ingest_chunks.py --json chunks_s1.json --file-id file_bns
    python ingest_chunks.py --json chunks_s1.json --local ./qdrant_local

Local embedded mode (no Docker needed):
    Set QDRANT_LOCAL_PATH=./qdrant_local in .env  OR  use --local flag.
    Data is stored as files in the given directory.
"""

import argparse
import json
import os
import sys
import uuid
import warnings
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, HnswConfigDiff, PayloadSchemaType,
    PointStruct, VectorParams,
)

warnings.filterwarnings("ignore", message="Api key is used with an insecure connection")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()

QDRANT_LOCAL_PATH = os.getenv("QDRANT_LOCAL_PATH", "")   # embedded mode if set
QDRANT_URL        = os.getenv("QDRANT_URL",       "http://localhost:6333")
QDRANT_API_KEY    = os.getenv("QDRANT_API_KEY",   "")
COLLECTION        = os.getenv("QDRANT_COLLECTION", "parent_child_chunks")
# Free open-weight embedding model — runs locally, no API key required.
# BAAI/bge-base-en-v1.5: 768-dim, ~440 MB, strong retrieval quality.
EMBEDDING_MODEL   = os.getenv("EMBEDDING_MODEL",   "BAAI/bge-base-en-v1.5")
VECTOR_DIM        = 768
BATCH_SIZE        = 32   # smaller batches for local CPU inference


def _normalize_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.path and parsed.path not in ("", "/"):
        url = urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))
    return url.rstrip("/")


def embed_batch(model: SentenceTransformer, texts: list[str]) -> list[list[float]]:
    # normalize_embeddings=True gives unit-length vectors (equivalent to cosine similarity)
    return model.encode(texts, normalize_embeddings=True, show_progress_bar=False).tolist()


def ensure_collection(qdrant: QdrantClient) -> None:
    existing = {c.name for c in qdrant.get_collections().collections}
    if COLLECTION in existing:
        print(f"[INFO] Collection '{COLLECTION}' already exists — reusing.")
        return
    print(f"[INFO] Creating collection '{COLLECTION}'...")
    qdrant.create_collection(
        collection_name=COLLECTION,
        vectors_config=VectorParams(size=VECTOR_DIM, distance=Distance.COSINE, on_disk=True),
        hnsw_config=HnswConfigDiff(m=16, ef_construct=200, on_disk=True),
        on_disk_payload=True,
    )
    for field, schema in [
        ("file_id",    PayloadSchemaType.TEXT),
        ("chunk_id",   PayloadSchemaType.TEXT),
        ("chunk_type", PayloadSchemaType.KEYWORD),
        ("is_delete",  PayloadSchemaType.BOOL),
        ("is_active",  PayloadSchemaType.BOOL),
        ("page_number",PayloadSchemaType.INTEGER),
        ("chunk_index",PayloadSchemaType.INTEGER),
        ("has_image",  PayloadSchemaType.BOOL),
    ]:
        qdrant.create_payload_index(COLLECTION, field_name=field, field_schema=schema)
    print("[INFO] Collection created.")


def upsert_batch(qdrant: QdrantClient, points: list[PointStruct]) -> None:
    try:
        qdrant.upsert(collection_name=COLLECTION, points=points)
    except Exception as e:
        print(f"[ERROR] Batch failed: {e} — retrying one-by-one")
        for pt in points:
            try:
                qdrant.upsert(collection_name=COLLECTION, points=[pt])
            except Exception as e2:
                print(f"  [SKIP] Point {pt.id}: {e2}")


def ingest(json_path: str, file_id: str) -> None:
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict):
        chunks = data.get("chunks", [])
        source = data.get("source", Path(json_path).name)
    else:
        chunks = data
        source = Path(json_path).name

    print(f"\nLoaded {len(chunks)} chunks  |  source: {source}  |  file_id: {file_id}")

    print(f"[INFO] Loading embedding model: {EMBEDDING_MODEL}")
    embed_model = SentenceTransformer(EMBEDDING_MODEL)
    if QDRANT_LOCAL_PATH:
        qdrant = QdrantClient(path=QDRANT_LOCAL_PATH)
        print(f"[INFO] Using embedded Qdrant at: {QDRANT_LOCAL_PATH}")
    else:
        qdrant = QdrantClient(
            url=_normalize_url(QDRANT_URL),
            api_key=QDRANT_API_KEY or None,
            check_compatibility=False,
        )
    ensure_collection(qdrant)

    ts = datetime.now(timezone.utc).isoformat()

    # ── Build parent sections grouped by top-2-level heading ─────────────────
    # Each unique heading_path[:2] key becomes one parent section in Qdrant.
    # parent_registry[top_2_heading] = {uuid, collected_text_parts, first_page}
    parent_registry: dict[str, dict] = {}

    for c in chunks:
        if "heading_path" in c:
            hp = c["heading_path"]
        elif "heading_text" in c:
            hp = [x.strip() for x in c["heading_text"].split(">") if x.strip()]
        else:
            hp = []

        # Use top 2 heading levels as parent key for more granular sections.
        # This avoids very large parents when a chapter has dozens of child chunks.
        top = " > ".join(hp[:2]) if len(hp) >= 2 else (hp[0] if hp else "__no_heading__")
        if top not in parent_registry:
            parent_registry[top] = {
                "uuid":       str(uuid.uuid4()),
                "text_parts": [],
                "page":       c.get("page_number", c.get("page", 1)),
            }
        text_val = c.get("content") or c.get("text") or ""
        parent_registry[top]["text_parts"].append(text_val)

    n_parents = len(parent_registry)
    print(f"Parent sections : {n_parents}")
    print(f"Child chunks    : {len(chunks)}\n")

    # ── Insert parents ────────────────────────────────────────────────────────
    print("[1/2] Embedding and inserting parent sections...")
    parent_keys = list(parent_registry.keys())

    for start in range(0, n_parents, BATCH_SIZE):
        batch_keys = parent_keys[start:start + BATCH_SIZE]
        texts = []
        for k in batch_keys:
            full_text = "\n\n".join(parent_registry[k]["text_parts"])
            if len(full_text) > 8000:
                full_text = full_text[:8000] + "..."
            texts.append(full_text)

        vectors = embed_batch(embed_model, texts)
        points = []
        for key, text, vec in zip(batch_keys, texts, vectors):
            reg = parent_registry[key]
            pid = reg["uuid"]
            points.append(PointStruct(
                id=pid,
                vector=vec,
                payload={
                    "vector_id":       pid,
                    "file_id":         file_id,
                    "filename":        source,
                    "chunk_id":        pid,
                    "chunk_type":      "parent",
                    "parent_chunk_id": "",
                    "page_number":     reg["page"],
                    "chunk_index":     0,
                    "original_text":   text,
                    "heading_text":    key,
                    "parent_heading":  key,
                    "section_heading": key,
                    "heading_level":   1,
                    "has_image":       False,
                    "token_count":     len(text.split()),
                    "doc_items_refs":  [],
                    "is_delete":       False,
                    "is_active":       True,
                    "timestamp":       ts,
                },
            ))
        upsert_batch(qdrant, points)
        print(f"  Parents {start + 1}–{start + len(batch_keys)} / {n_parents} inserted")

    # ── Insert children ───────────────────────────────────────────────────────
    print(f"\n[2/2] Embedding and inserting {len(chunks)} child chunks...")

    for start in range(0, len(chunks), BATCH_SIZE):
        batch = chunks[start:start + BATCH_SIZE]

        # Build heading info first so we can prepend it to the embedding text.
        batch_meta = []
        for c in batch:
            if "heading_path" in c:
                hp = c["heading_path"]
            elif "heading_text" in c:
                hp = [x.strip() for x in c["heading_text"].split(">") if x.strip()]
            else:
                hp = []
            raw_text = c.get("content") or c.get("text") or ""
            if len(raw_text) > 8000:
                raw_text = raw_text[:8000] + "..."
            heading_text = " > ".join(hp) if hp else ""
            # Prepend heading path to the text used for embedding so the model
            # knows which section the fragment belongs to (improves retrieval).
            embed_text = f"{heading_text}\n\n{raw_text}" if heading_text else raw_text
            batch_meta.append((hp, raw_text, heading_text, embed_text))

        texts_for_embed = [m[3] for m in batch_meta]
        vectors = embed_batch(embed_model, texts_for_embed)
        points = []
        for i, (c, (hp, raw_text, heading_text, _), vec) in enumerate(
            zip(batch, batch_meta, vectors)
        ):
            top = " > ".join(hp[:2]) if len(hp) >= 2 else (hp[0] if hp else "__no_heading__")
            parent_uuid = parent_registry[top]["uuid"]
            child_uuid = str(uuid.uuid4())
            chunk_id = c.get("chunk_id") or str(uuid.uuid4())
            text = raw_text  # store original text (without heading prefix) for display

            points.append(PointStruct(
                id=child_uuid,
                vector=vec,
                payload={
                    "vector_id":       child_uuid,
                    "file_id":         file_id,
                    "filename":        source,
                    "chunk_id":        chunk_id,
                    "chunk_type":      "child",
                    "parent_chunk_id": parent_uuid,
                    "page_number":     c.get("page_number", c.get("page", 1)),
                    "chunk_index":     start + i,
                    "original_text":   text,
                    "heading_text":    heading_text,
                    "parent_heading":  top,
                    "section_heading": heading_text,
                    "heading_level":   c.get("heading_level", len(hp)),
                    "has_image":       False,
                    "token_count":     len(text.split()),
                    "doc_items_refs":  [],
                    "is_delete":       False,
                    "is_active":       True,
                    "timestamp":       ts,
                },
            ))
        upsert_batch(qdrant, points)
        print(f"  Children {start + 1}–{start + len(batch)} / {len(chunks)} inserted")

    print(f"\nDone.")
    print(f"  {n_parents} parent sections + {len(chunks)} child chunks -> '{COLLECTION}'")
    print(f"  file_id: {file_id}")
    print(f"\n  Query with:  python rag.py \"your question\"")
    print(f"  Restrict to this file:  python rag.py --file-id {file_id} \"your question\"")


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest new_docling chunks into Qdrant for rag.py")
    parser.add_argument("--json",    default="chunks_s1.json", help="Path to chunks JSON file")
    parser.add_argument("--file-id", default=None, dest="file_id",
                        help="Optional file_id to tag all points (auto-generated if omitted)")
    parser.add_argument("--local",   default=None, dest="local_path",
                        help="Path for embedded Qdrant storage (overrides QDRANT_LOCAL_PATH)")
    args = parser.parse_args()

    # CLI --local overrides env var
    if args.local_path:
        import os as _os
        _os.environ["QDRANT_LOCAL_PATH"] = args.local_path
        global QDRANT_LOCAL_PATH
        QDRANT_LOCAL_PATH = args.local_path

    if not os.path.exists(args.json):
        print(f"[ERROR] JSON file not found: {args.json}")
        sys.exit(1)

    file_id = args.file_id or ("file_" + str(uuid.uuid4())[:8])
    ingest(args.json, file_id)


if __name__ == "__main__":
    main()
