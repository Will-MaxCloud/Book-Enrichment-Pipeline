# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Git Workflow

Commit and push to GitHub regularly throughout any work session — after each meaningful change, not just at the end. This ensures no progress is lost and the repo always reflects current state.

```bash
git add <specific files>
git commit -m "short, descriptive message in present tense"
git push
```

Keep commit messages specific (e.g., `"add romance sub-score to SlimChunkAnalysis"` not `"update analyzer"`). Never use `git add -A` or `git add .` without reviewing what's staged first.

## Project Overview

RecShelf_pipe2.0 (Genius Book Analysis v4) is a Python pipeline that analyzes PDF and EPUB books using the Claude API, producing structured JSON with literary analysis: themes, characters, content ratings, reading metrics, and more.

## Running the Pipeline

```bash
# Activate virtual environment first
source .venv/Scripts/activate   # Windows Git Bash
# or: .venv\Scripts\activate    # Windows CMD

# Single book (quality mode — Sonnet for both passes)
python run_analysis.py /path/to/book.pdf

# Fast mode (Haiku for Pass 1, ~10x cheaper)
python run_analysis.py /path/to/book.pdf --fast

# Parallel chunk processing (~5x faster)
python run_analysis.py /path/to/book.pdf --parallel

# Batch process a directory
python run_analysis.py /path/to/books/ --batch --fast

# Custom output directory
python run_analysis.py /path/to/book.pdf --output ./results/

# Override models explicitly
python run_analysis.py book.pdf --p1-model claude-haiku-4-5-20251001 --p2-model claude-sonnet-4-20250514
```

Requires `ANTHROPIC_API_KEY` in environment (or pass `--api-key`).

Dependencies: `pip install pymupdf anthropic pydantic ebooklib beautifulsoup4`

## Architecture: Two-Pass Pipeline

```
PDF/EPUB → [extractor.py] → chunks + opening/closing text
                               ↓
              [analyzer.py] Pass 1: per-chunk analysis
              (ChunkAnalysis or SlimChunkAnalysis per chunk)
                               ↓
              [analyzer.py] Pass 2: holistic full-book analysis
              (opening + closing text + chunk summaries → HolisticAnalysis)
                               ↓
              [analyzer.py] Aggregate: weighted-average ratings,
              union flags, merge characters → BookAnalysis (Pydantic)
                               ↓
              [run_analysis.py] Save → {title}_analysis.json
```

**Why two passes:** Pass 1 captures granular per-scene details (e.g., a violent chapter 7); Pass 2 captures whole-book patterns (recurring tone, final character arcs, genre). Neither pass alone is sufficient.

**Model selection:**
- Pass 1 quality: `claude-sonnet-4-20250514` (full `ChunkAnalysis` with character arcs, themes, etc.)
- Pass 1 fast: `claude-haiku-4-5-20251001` (slim scoring only via `SlimChunkAnalysis`)
- Pass 2: always Sonnet (holistic reasoning requires higher capability)

## Key Files

| File | Purpose |
|------|---------|
| `run_analysis.py` | CLI entry point, pipeline orchestration, `analyze_book()` |
| `analyzer.py` | `AnalysisClient` class — all Claude API calls, prompt builders, aggregation logic |
| `schemas.py` | All Pydantic models, enums, and master taxonomy lists |
| `extractor.py` | PDF/EPUB text extraction, chapter detection, chunking |
| `compare.py` | Utilities for comparing analysis outputs across runs/models |

## Schemas and Taxonomies (`schemas.py`)

All valid values for themes, genres, sub-genres, categories, and archetypes are defined as master lists and enums in `schemas.py`:
- `MASTER_THEMES` — 70+ canonical theme strings
- `MASTER_SUB_GENRES` — 200+ sub-genre strings
- `MASTER_CATEGORIES` — 100+ category strings
- `Genre` enum — 47 genres
- `CharacterArchetype` enum — 21 archetypes
- `ContentFlag` enum — 15 content flags
- `SCORE_ANCHORS` — semantic descriptions for 1–10 ratings

When modifying prompts or adding new dimensions, always reference these master lists. LLM outputs are sanitized/mapped to valid values in `analyzer.py` (`_sanitize_chunk_data`, `_sanitize_holistic_data`).

Chunk size: max 5000 words per chunk, 300-word overlap for windowed (non-chapter) chunking.

## Design Decisions

- **Temperature 0.0** — hardcoded for consistency across repeated runs
- **No Instructor library** — silent retries caused issues; direct JSON parsing with `_safe_parse_json()` is used instead
- **Prompt caching** — system prompts (which include large taxonomy lists) use `ephemeral` cache control to reduce cost/latency
- **Max 2 concurrent requests** — semaphore-controlled in parallel mode to avoid rate limits; 1s stagger between launches
- **Retry logic** — 3 attempts with exponential backoff (2s, 4s, 8s); chunk failures are logged and skipped rather than halting the pipeline
- **Pydantic validation** — character arcs are never left empty (enforced); themes must come from `MASTER_THEMES`
