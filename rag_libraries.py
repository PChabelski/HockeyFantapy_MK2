"""
rag_module.py

Drop-in RAG module for fantasy hockey pipeline.
Exposes two public functions:

    index(df)        — call after your data pipeline finishes
    ask(query, ...)  — call to answer a natural language question

Everything else is internal.
"""

import os
import pandas as pd
import chromadb
from sentence_transformers import SentenceTransformer, CrossEncoder
from rank_bm25 import BM25Okapi
from openai import OpenAI

# --- Config ---
_EMBED_MODEL     = "BAAI/bge-small-en-v1.5"
_RERANK_MODEL    = "cross-encoder/ms-marco-MiniLM-L-6-v2"
_COLLECTION_NAME = "hockey_daily"
_TOP_K_RETRIEVAL = 20
_TOP_K_RERANK    = 8
_RRF_K           = 60
_BATCH_SIZE      = 512

# --- Internal state ---
# These are module-level singletons — loaded once, reused forever.
# Prefixed with _ to signal they're private to this module.
_embedder   = None
_reranker   = None
_db_client  = None
_collection = None
_bm25       = None
_all_chunks = []
_openai     = None


def _load_models():
    """Load all models once. Subsequent calls are instant."""
    global _embedder, _reranker, _db_client, _openai

    if _embedder is None:
        print("[rag] Loading embedding model...")
        _embedder = SentenceTransformer(_EMBED_MODEL)

    if _reranker is None:
        print("[rag] Loading reranker...")
        _reranker = CrossEncoder(_RERANK_MODEL)

    if _db_client is None:
        _db_client = chromadb.PersistentClient(path="./chroma_store")

    if _openai is None:
        _openai = OpenAI()


# --- Row to text ---
def _safe_int(value, default=0) -> int:
    """Convert a value to int safely, returning default if blank or NaN."""
    try:
        if value is None or str(value).strip() in ("", "nan", "NaN"):
            return default
        return int(float(value))
    except (ValueError, TypeError):
        return default


def _row_to_text(row: dict) -> str:
    name     = row.get("NAME", "Unknown")
    date     = row.get("DATE", "?")
    position = row.get("DISPLAY_POSITION", "?")
    owner    = row.get("OWNER_TEAM_NAME", "FREE_AGENT")
    gm       = row.get("OWNER_TEAM_GM", "")

    if owner == "FREE_AGENT":
        fantasy = f"{name} is a free agent."
    else:
        selected = row.get("SELECTED_POSITION", "")
        missed   = row.get("MISSED_START", 0)
        if missed == 1:
            fantasy = (
                f"{name} is on {gm}'s team ({owner}) "
                f"but was a missed start."
            )
        elif selected:
            fantasy = (
                f"{name} is on {gm}'s team ({owner}), "
                f"played at {selected}."
            )
        else:
            fantasy = (
                f"{name} is on {gm}'s team ({owner}), "
                f"no game this day."
            )

    # Detect goalie vs skater by whether GOALIE_TOI is populated
    goalie_toi = row.get("GOALIE_TOI", None)
    is_goalie = (
            goalie_toi is not None and
            str(goalie_toi).strip() not in ("", "nan", "NaN", "0", "0.0")
    )

    if is_goalie:
        w = row.get("W", None)
        if w is None or str(w).strip() in ("", "nan"):
            stats = "No game."
        else:
            stats = (
                f"{_safe_int(w)}W "
                f"{_safe_int(row.get('L', 0))}L "
                f"GAA:{row.get('GAA', '?')} "
                f"SV%:{row.get('SV%', '?')} "
                f"SO:{_safe_int(row.get('SO', 0))} "
                f"TOI:{goalie_toi}"
            )
    else:
        g = row.get("G", None)
        gp = row.get("GAME_PLAYED", None)

        # Use GAME_PLAYED as the authoritative signal for whether
        # a player had a game — not whether G is blank
        had_game = (
                gp is not None and
                str(gp).strip() not in ("", "nan", "0", "0.0")
        )

        if not had_game:
            stats = "No game."
        else:
            stats = (
                f"{_safe_int(g)}G "
                f"{_safe_int(row.get('A', 0))}A "
                f"{_safe_int(row.get('PTS', 0))}PTS "
                f"{_safe_int(row.get('PIM', 0))}PIM "
                f"{_safe_int(row.get('PPP', 0))}PPP "
                f"{_safe_int(row.get('S', 0))}SOG "
                f"TOI:{row.get('TOI', '?')}"
            )

    return f"[{date}] {fantasy} Pos:{position}. Stats: {stats}"


# --- Public API ---

def index(df: pd.DataFrame):
    """
    Index your dataframe into the RAG system.
    Call this once after your data pipeline finishes loading data.
    Safe to call again when data refreshes — wipes and rebuilds cleanly.

    Args:
        df: your cleaned fantasy hockey DataFrame
    """
    global _collection, _bm25, _all_chunks

    _load_models()

    # Drop rows with no useful content
    # (FREE_AGENT + no game = nothing to retrieve)
    mask = ~(
        (df["OWNER_TEAM_NAME"] == "FREE_AGENT") &
        (df["G"].isna()) &
        (df["GOALIE_TOI"].isna())
    )
    df = df[mask].copy()
    print(f"[rag] Indexing {len(df):,} rows...")

    # Rebuild collection fresh
    try:
        _db_client.delete_collection(_COLLECTION_NAME)
    except Exception:
        pass

    _collection = _db_client.create_collection(
        name               = _COLLECTION_NAME,
        embedding_function = None
    )

    records    = df.to_dict(orient="records")
    _all_chunks = []
    all_ids    = []
    all_meta   = []

    for i, record in enumerate(records):
        text = _row_to_text(record)
        _all_chunks.append(text)
        all_ids.append(f"row_{i}")
        all_meta.append({
            "date":  str(record.get("DATE",             "")),
            "gm":    str(record.get("OWNER_TEAM_GM",    "")),
            "owner": str(record.get("OWNER_TEAM_NAME",  "")),
            "pos":   str(record.get("DISPLAY_POSITION", ""))
        })

    # Embed in batches
    total = len(_all_chunks)
    for start in range(0, total, _BATCH_SIZE):
        end        = min(start + _BATCH_SIZE, total)
        batch_text = _all_chunks[start:end]
        batch_ids  = all_ids[start:end]
        batch_meta = all_meta[start:end]

        embeddings = _embedder.encode(
            batch_text,
            normalize_embeddings = True,
            show_progress_bar    = False,
            batch_size           = 64
        ).tolist()

        _collection.add(
            ids        = batch_ids,
            documents  = batch_text,
            embeddings = embeddings,
            metadatas  = batch_meta
        )
        print(f"[rag] {min(end, total):,} / {total:,} rows indexed")

    # BM25
    print("[rag] Building BM25 index...")
    _bm25 = BM25Okapi([c.lower().split() for c in _all_chunks])
    print("[rag] Indexing complete.")


def load_existing(df: pd.DataFrame):
    """
    Skip re-embedding and reload from existing ChromaDB.
    Use this when data hasn't changed since last index() call.
    Still needs df to rebuild BM25 and _all_chunks in memory.
    Runs in ~30-60 seconds instead of 8-12 minutes.
    """
    global _collection, _bm25, _all_chunks

    _load_models()

    # Reconnect to existing ChromaDB collection
    try:
        _collection = _db_client.get_collection(
            name               = _COLLECTION_NAME,
            embedding_function = None
        )
        print(f"[rag] Loaded existing collection: "
              f"{_collection.count():,} chunks")
    except Exception:
        print("[rag] No existing collection found. "
              "Run rag_libraries.index(df) first.")
        return

    # Apply same filter as index() so chunk order matches ChromaDB
    mask = ~(
        (df["OWNER_TEAM_NAME"] == "FREE_AGENT") &
        (df["G"].isna()) &
        (df["GOALIE_TOI"].isna())
    )
    df = df[mask].copy()

    # Rebuild chunk list and BM25 in memory — no embedding needed
    records     = df.to_dict(orient="records")
    _all_chunks = [_row_to_text(r) for r in records]

    print("[rag] Rebuilding BM25 index...")
    _bm25 = BM25Okapi([c.lower().split() for c in _all_chunks])
    print("[rag] Ready.")

def ask(
    query:       str,
    gm_filter:   str | None = None,
    date_filter: str | None = None
) -> str:
    """
    Ask a natural language question about your fantasy hockey data.

    Args:
        query:       plain English question
        gm_filter:   restrict search to one GM's roster
                     e.g. gm_filter="Patrick"
        date_filter: restrict search to one date
                     e.g. date_filter="2025-10-07"

    Returns:
        plain English answer string

    Examples:
        ask("Who has the most points this season?")
        ask("How many missed starts has Patrick had?", gm_filter="Patrick")
        ask("Who played on October 7th?", date_filter="2025-10-07")
        ask("Which of Patrick's players scored last Tuesday?",
            gm_filter="Patrick", date_filter="2025-10-07")
    """
    if _collection is None or _bm25 is None:
        return "[rag] Not indexed yet. Call rag_module.index(df) first."

    _load_models()

    # Vector retrieval
    qe = _embedder.encode(query, normalize_embeddings=True).tolist()

    where = None
    if gm_filter and date_filter:
        where = {"$and": [
            {"gm":   {"$eq": gm_filter}},
            {"date": {"$eq": date_filter}}
        ]}
    elif gm_filter:
        where = {"gm":   {"$eq": gm_filter}}
    elif date_filter:
        where = {"date": {"$eq": date_filter}}

    vec_kwargs = {
        "query_embeddings": [qe],
        "n_results":        min(_TOP_K_RETRIEVAL, len(_all_chunks))
    }
    if where:
        vec_kwargs["where"] = where

    vec_results = _collection.query(**vec_kwargs)
    vec_chunks  = vec_results["documents"][0]

    # BM25 retrieval
    bm25_scores  = _bm25.get_scores(query.lower().split())
    top_bm25_idx = sorted(
        range(len(bm25_scores)),
        key     = lambda i: bm25_scores[i],
        reverse = True
    )[:_TOP_K_RETRIEVAL]
    bm25_chunks = [_all_chunks[i] for i in top_bm25_idx]

    # RRF merge
    scores = {}
    for rank, chunk in enumerate(vec_chunks):
        scores[chunk] = scores.get(chunk, 0) + 1 / (_RRF_K + rank + 1)
    for rank, chunk in enumerate(bm25_chunks):
        scores[chunk] = scores.get(chunk, 0) + 1 / (_RRF_K + rank + 1)

    merged = sorted(
        scores, key=lambda c: scores[c], reverse=True
    )[:_TOP_K_RETRIEVAL]

    # Rerank
    pairs     = [(query, chunk) for chunk in merged]
    rr_scores = _reranker.predict(pairs)
    ranked    = sorted(
        zip(rr_scores, merged),
        key     = lambda x: x[0],
        reverse = True
    )
    context_chunks = [chunk for score, chunk in ranked[:_TOP_K_RERANK]]

    # Generate
    context  = "\n\n".join(context_chunks)
    response = _openai.chat.completions.create(
        model    = "gpt-4o-mini",
        messages = [{
            "role":    "user",
            "content": (
                f"You are a fantasy hockey analyst.\n"
                f"Answer using only the player data below.\n"
                f"Be specific — include names, stats, dates, "
                f"and GM names where relevant.\n\n"
                f"Data:\n{context}\n\n"
                f"Question: {query}"
            )
        }],
        temperature = 0.2
    )
    return response.choices[0].message.content.strip()