# HockeyFantapy MK2

A multi-season fantasy hockey analytics pipeline that extracts daily data from two independent sources — the Yahoo Fantasy Sports API and Hockey Reference — reconciles player identities across them via fuzzy name matching, computes a custom set of fantasy metrics, and exposes the resulting dataset through both a structured pandas aggregation layer and a hybrid retrieval-augmented generation (RAG) query interface backed by dense embeddings, BM25, reciprocal rank fusion, and cross-encoder reranking.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  EXTRACTION LAYER   (generic.py / main.py / daily_runner.py)                │
│                                                                             │
│  daily_runner.py ──► main.py --mode 2 ──► YEAR_INSTANCE (generic.py)       │
│                                                                             │
│  Yahoo Fantasy API (yfpy)          Hockey Reference (requests + BS4)        │
│  ─────────────────────────         ─────────────────────────────────        │
│  • League metadata, teams,         • Full-season NHL schedule                │
│    standings, scoreboard           • Per-game box scores — skater stats      │
│  • Daily per-team rosters            (G, A, PTS, +/-, PIM, PP/SH, SOG,     │
│    with lineup slot selections       TOI, Corsi) and goalie stats            │
│  • Transactions (adds/drops/         (DEC, GA, SA, SV%, SO)                 │
│    trades), draft results                                                    │
│                                                                             │
│  Fuzzy name matching (RapidFuzz)                                            │
│  ──────────────────────────────                                             │
│  Yahoo display names ◄──► Hockey Reference slugs                            │
│  Persisted in PLAYER_MASTER_DATA.csv; reviewed manually for low-confidence  │
│  matches and known collisions (same-name players disambiguated by player ID) │
│                                                                             │
│  Output: per-year CSV folders (gitignored)                                  │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  ANALYTICS LAYER   (analytics_engine.py / analytics_main.py)                │
│                                                                             │
│  ANALYTICS_ENGINE reads the raw CSVs — no API calls made here.              │
│                                                                             │
│  link_hr_days_to_yh_days()  — outer-join Yahoo roster + HR box scores      │
│                                on HR_LINK_NAME slug; derive PPP, SHP,       │
│                                fantasy points, MISSED_START flag            │
│  yearly_stat_roster_combiner() — concatenate daily merges into one          │
│                                  per-season CSV for downstream use          │
│  Specialized analytics:                                                     │
│    matchup_analytics        head-to-head weekly results + stat quality      │
│    power_ranking_analytics  monthly aggregate power rankings                │
│    keeper_analytics         keeper ROI (FP generated while kept)            │
│    draft_analytics          draft pick ROI by round                         │
│    FAAB_analytics           FAAB spend efficiency                           │
│    streamer_analytics       short-term pickup performance by week           │
│    engagement_analytics     missed starts, add activity, goalie compliance  │
│    hospital_analytics       player injury streak tracking                   │
│    loyalty_analytics        fraction of drafted/kept roster held all season │
│                                                                             │
│  Output: analytics_*/ folders + csv_databases/ aggregates for Power BI     │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  QUERY LAYER   (rag_libraries.py / rag_runner.py)             [WIP]         │
│                                                                             │
│  rag_runner.py loads the yearly merged CSV, then either re-indexes          │
│  from scratch or reconnects to an existing ChromaDB store.                  │
│                                                                             │
│  At index time:                                                             │
│    1. Each player-day row is serialized to a natural language chunk         │
│    2. Chunks are embedded with BGE and stored in ChromaDB                   │
│    3. A BM25Okapi index is built in parallel                                │
│                                                                             │
│  At query time:                                                             │
│    1. Dense retrieval  — BGE query embedding → ChromaDB ANN search         │
│    2. Sparse retrieval — BM25 term-frequency scoring over all chunks        │
│    3. RRF merge        — reciprocal rank fusion combines both result lists  │
│    4. Cross-encoder rerank — ms-marco MiniLM re-scores the merged set      │
│    5. GPT-4o-mini generation — top-K chunks passed as context              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Why a Separate Pandas Analytics Layer?

RAG is well-suited to open-ended retrieval — "which players were benched during a hot streak?" — but breaks down for exact numeric aggregation. An LLM cannot reliably sum or average across dozens of retrieved chunks; it approximates. Asking "what is each GM's average FAAB spend efficiency across all seasons?" through a RAG interface will produce plausible-sounding but arithmetically unreliable answers.

The pandas layer solves this by computing aggregations over the full dataset before any query is issued. Metrics like MISSED_START counts, keeper ROI, and FAAB efficiency are calculated exactly at analytics time and stored as output CSVs. The RAG layer is then reserved for the class of questions where semantic retrieval and natural language fluency matter more than numeric precision — contextual lookups, pattern recognition across narrative descriptions, and exploratory questions that do not reduce to a single group-by.

This two-layer design is intentional: structured questions route to `analytics_engine.py`, semantic questions route to `rag_libraries.py`.

---

## Key Technical Components

| Component | Library | Role |
|---|---|---|
| **Dense embeddings** | `sentence-transformers` — `BAAI/bge-small-en-v1.5` | Encodes player-day text chunks into vectors for semantic similarity search. BGE (BAAI General Embedding) is an open-source model tuned for retrieval tasks. |
| **Vector store** | `chromadb` (persistent) | Stores embedded chunks on disk; supports fast approximate nearest-neighbor search and metadata filtering (by GM, by date). |
| **Sparse retrieval** | `rank_bm25` — BM25Okapi | Classic term-frequency / inverse document frequency scoring. Catches exact keyword matches (player names, stat abbreviations) that dense retrieval can miss. |
| **Reciprocal Rank Fusion (RRF)** | Custom implementation | Combines the dense and sparse ranked lists into a single merged ranking using the formula `score += 1 / (k + rank)` where k=60. RRF is parameter-light and robust to score-scale mismatches between the two retrievers. |
| **Cross-encoder reranker** | `sentence-transformers` — `cross-encoder/ms-marco-MiniLM-L-6-v2` | Re-scores the merged top-K candidates by jointly encoding the query and each candidate chunk together, giving more accurate relevance judgements than bi-encoder cosine similarity alone. |
| **LLM answer generation** | `openai` — `gpt-4o-mini` | Generates the final natural language answer grounded in the reranked context chunks. |
| **Fuzzy name matching** | `rapidfuzz` — `token_sort_ratio` | Reconciles Yahoo display names with Hockey Reference slugs. Applies ASCII normalization, first-name abbreviation substitution, and diacritic stripping before scoring. Matches below a confidence threshold are flagged for manual review. |
| **Structured analytics** | `pandas`, `scipy.stats.zscore`, `duckdb` | All exact aggregations, metric derivations, and multi-season roll-ups. DuckDB reserved for future SQL interface. |

---

## Setup

### Prerequisites

- Python 3.10+
- Yahoo Fantasy Sports API credentials (consumer key + consumer secret)
- An OpenAI API key

### Install dependencies

```bash
pip install yfpy rapidfuzz beautifulsoup4 lxml requests pandas numpy \
            scipy sentence-transformers chromadb rank_bm25 openai \
            duckdb python-dotenv unidecode
```

### Environment variables

Create a `.env` file in the project root:

```
OPENAI_API_KEY=[INSERT YOUR OPENAI API KEY HERE]
```

### Yahoo OAuth

Place your Yahoo OAuth token file in a `private/` directory at the project root. On first run, `yfpy` will open a browser window to complete the OAuth flow and will write the token to `private/`. Subsequent runs refresh automatically.

### Runtime configuration

The pipeline is driven by `manual_data/control_file.json` (gitignored), which contains per-season league IDs, game IDs, scoring category weights, keeper lists, and draft dates. A `manual_data/PLAYER_MASTER_DATA.csv` file persists the fuzzy-matched Yahoo ↔ Hockey Reference player identity map across seasons.

### Running the pipeline

```bash
# Daily extraction (run automatically by Windows Task Scheduler via daily_runner.py)
python main.py --mode 2

# Manual / historical extraction with method selection
python main.py --mode 1

# Analytics over extracted CSVs
python analytics_main.py

# Interactive natural language query loop
python rag_runner.py
```

---

## Data Flow Summary

```
Yahoo API + Hockey Reference
        │
        ▼
  Per-day raw CSVs  (team_rosters_by_date/, hr_data_extract/)
        │
        ▼
  Fuzzy name reconciliation  →  PLAYER_MASTER_DATA.csv
        │
        ▼
  Merged daily CSVs  (merged_extracts/<year>/)
        │
        ├──► Yearly combined CSV  (merged_extracts_years/<year>_ALL_DATA.csv)
        │              │
        │              └──► RAG index (ChromaDB + BM25)
        │
        └──► Analytics outputs  (analytics_*/, csv_databases/ for Power BI)
```

---

## What's in Progress

- **RAG query router**: `rag_runner.py` currently routes all queries through the RAG pipeline. The intended design adds a classifier that detects structured aggregation questions and dispatches them to `analytics_engine.py` instead, falling back to RAG for everything else.
- **Yahoo API access**: Yahoo's developer program paused new API key approvals; the extraction layer is complete and tested but blocked on credential access for new seasons.
- **DuckDB SQL interface**: `duckdb` is imported and the `sql_tables/` output directory is defined but the SQL layer has not been built out yet.
- **Multi-season RAG**: the current RAG index covers a single season CSV; extending it to span multiple seasons with year-aware metadata filtering is planned.
