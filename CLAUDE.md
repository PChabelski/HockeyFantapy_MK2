# HockeyFantapy_MK2

## Project Overview
Fantasy hockey analytics pipeline for a multi-GM Yahoo Fantasy Hockey 
league. Pulls daily NHL and Yahoo Fantasy Hockey data, calculates custom 
metrics, and produces summary CSVs for dashboarding and analysis.

## Key Modules
- rag_libraries.py     — hybrid RAG pipeline (BGE + BM25 + RRF + reranker) [WIP]
- rag_runner.py        — interactive query loop, routes between RAG/pandas [WIP]
- analytics_engine.py         — pandas aggregation layer for structured queries
- analytics_main.py    - This is what you run to pull specific analytics queries
- generic.py           - This contains all the algos needed to pull and parse online data
- main.py              - This is what you run to run specific generic.py subfunctions
- daily_runner.py      - This is called by the windows scheduler every day (when scheduled) to run main/generic subfunctions

## Data Flow
Daily Yahoo/NHL extraction
    → fuzzy player name matching (yahoo ↔ NST/HR data sources)
    → merge and clean into daily CSVs
    → calculate custom metrics (MISSED_START, Z-scores, etc.)
    → roll up into yearly summary CSVs
    → dashboarding via Power BI / visualization scripts

## Key Custom Metrics
- MISSED_START: player on GM roster had an NHL game but was left 
  on the bench — not placed in an active fantasy lineup slot
- Z-scores: normalized performance metrics across the league

## Data Sources
- Yahoo Fantasy Hockey API (via yfpy)
- Natural Stat Trick (NST) — advanced hockey stats
- Hockey Reference (HR) — historical data

## Branch Strategy
- master: production, never commit directly
- release/x.x: release candidates
- dev/x.x.x: feature development
- claude/: AI-assisted changes, always reviewed before merging

## Rules
- Never modify source CSVs directly
- All data folders are gitignored — see .gitignore
- chroma_store/ rebuilds automatically via rag_runner.py
- OPENAI_API_KEY lives in .env — never commit this
- All Claude Code changes go on a claude/ branch first

## Style Preferences
- pandas over raw Python loops for data operations
- Docstrings on all public functions
- Progress print statements are intentional — keep them
- f-strings preferred over .format()

## Do Not Touch
- Any folder listed in .gitignore
- control_file.json — runtime configuration
- private/ folder