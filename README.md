# Book Enrichment Pipeline

Converts PDF and EPUB books into structured literary analysis JSON. Designed to feed a book discovery platform where every output field is a filter, tag, or rating — not a summary to read, but data to search, rank, and recommend by.

This is a **classification and scoring engine**, not a summarizer. Output goes directly to production with no human review step, so reliability is the primary constraint.

---

## How it works

```
PDF/EPUB
   │
   ▼
[extractor.py]  ── chapter detection, chunking, back-matter trimming
   │
   ▼
[Pass 1 — per chunk]  ── 7 numeric scores + character names + content flags
   │                       (Haiku in --fast; one call per chunk)
   ▼
[Pass 2 — holistic]  ── genre, themes, categories, characters, setting, summary
   │                     (Sonnet; one call for the whole book)
   ▼
[Aggregation]  ── weighted-average ratings, dedup, rank, merge
   │
   ▼
{title}_analysis.json
```

**Why two passes?**

Early versions did full analysis per chunk. This caused three problems:

1. **Theme inconsistency.** The same theme surfaced with slightly different wording chunk to chunk; averaging produced noise rather than signal.
2. **Character ranking instability.** A minor character scoring importance 8 in their one scene looked as significant as the protagonist averaging 7 across 30 chunks.
3. **Cost.** Full prompts cost ~10x more per chunk than slim scoring prompts.

Pass 1 handles what needs chunk-by-chunk measurement (ratings). Pass 2 handles what needs whole-book judgment (classifications). Neither works alone.

---

## Setup

```bash
# Create and activate virtual environment
python -m venv .venv
source .venv/Scripts/activate   # Windows Git Bash
# or: .venv\Scripts\activate    # Windows CMD / PowerShell

# Install dependencies
pip install anthropic pydantic pymupdf ebooklib beautifulsoup4

# Set API key
export ANTHROPIC_API_KEY=sk-ant-...
```

Requires Python 3.10+.

---

## Running it

```bash
# Single book — fast mode (recommended default)
python run_analysis.py book.epub --fast

# Batch process a directory
python run_analysis.py ./books/ --batch --fast

# Custom output directory
python run_analysis.py book.pdf --fast --output ./results/

# Override models explicitly
python run_analysis.py book.pdf --p1-model claude-haiku-4-5-20251001 --p2-model claude-sonnet-4-20250514
```

| Flag | Description |
|------|-------------|
| `--fast` | Pass 1 uses Haiku (slim scoring only), Pass 2 uses Sonnet. ~10x cheaper and ~3x faster than quality mode. **This is the practical default.** |
| *(no flag)* | Quality mode: both passes use Sonnet. Best output, but too slow and costly for production use. |
| `--batch` | Process all PDFs/EPUBs in a directory. Writes a `batch_profile.json` aggregating cost/timing stats. |
| `--output DIR` | Output directory (defaults to book's directory). |
| `--api-key KEY` | Pass key directly instead of env var. |
| `--p1-model MODEL` | Override Pass 1 model. |
| `--p2-model MODEL` | Override Pass 2 model. |

---

## Output schema

See `fic_example_output.json` (The Great Gatsby) and `nonfic_example_output.json` (Sapiens) for complete examples of both output types.

| Field | Type | Description |
|-------|------|-------------|
| `metadata.genre` | enum (41 values) | Primary genre — one of the controlled Genre enum values |
| `metadata.sub_genres` | string[] (1–4) | From a master list of 200+ sub-genres |
| `themes` | object[] (max 10) | `{name, prominence}` — name from MASTER_THEMES (~70 options); sorted by prominence |
| `categories` | string[] (exactly 3) | Discovery tags from MASTER_CATEGORIES (~100 options): vibes, tropes, settings, narrative styles |
| `characters` | object[] (max 8) | `{name, importance, gender, archetypes, arc_summary, age_category}` — sorted by importance |
| `setting` | object | `{primary_location, time_period, setting_type, real_or_fictional, additional_locations}` |
| `ratings` | object | 8 scores (1–10): `tone`, `readability`, `violence`, `pace`, `worldbuilding`, `humor`, `romance`, `age_target` |
| `computed_stats` | object | `total_words`, `total_pages`, `avg_sentence_length`, `avg_words_per_page`, `estimated_read_time_hours` |
| `reading_experience` | enum[] (1–4) | Descriptors like `immersive`, `feel_good`, `tense`, `cozy` |
| `humor` | object | `{humor_density (1–10), primary_humor_types[]}` |
| `pov` | enum[] | One or more of `first_person`, `third_person_limited`, `third_person_omniscient`, `second_person`, `multiple` |
| `content_flags` | enum[] (max 4) | Content warnings only — must be genuinely present in the text (see design decisions) |
| `sad_ending` | bool | |
| `cliffhanger` | bool | |
| `overall_summary` | string | 3–5 sentence synthesis |
| `non_fiction_info` | object \| null | Populated only for non-fiction books (thesis, target audience, structure type, sub-type addendum) |

All rating scales are anchored to concrete book examples (e.g., tone 1 = Winnie the Pooh, tone 10 = Blood Meridian) to prevent drift across runs.

---

## Files

| File | Purpose |
|------|---------|
| `run_analysis.py` | CLI entry point and pipeline orchestration |
| `analyzer.py` | All Claude API calls, prompt builders, sanitization/mapping tables, aggregation logic |
| `extractor.py` | PDF/EPUB text extraction, chapter detection, chunking, back-matter trimming |
| `schemas.py` | Pydantic models, enums, and master taxonomy lists (MASTER_THEMES, MASTER_CATEGORIES, etc.) |
| `ratelimiter.py` | Rolling-window rate limiter (RPM + TPM) to prevent 429s under concurrent load |
| `profiler.py` | Per-call and per-stage instrumentation — records tokens, latency, cost per run |
| `compare.py` | Compares two analysis JSON files of the same book, produces a similarity score (0–100) |
| `fic_example_output.json` | Example fiction output (The Great Gatsby, v4.0.0 schema) |
| `nonfic_example_output.json` | Example non-fiction output (Sapiens, v4.0.0 schema with `non_fiction_info` block) |

---

## Key design decisions

**Controlled vocabularies everywhere.** Genre, themes, sub-genres, categories, archetypes, setting types — every classification field uses a master list defined in `schemas.py`. The model can return synonyms, variations, or misspellings; the sanitization tables in `analyzer.py` normalize everything to the canonical value. If something doesn't map, it's dropped — not passed through as garbage.

**Temperature 0, always.** Same book = same output. The only acceptable variance is ±1 on numeric ratings. Classifications must be deterministic.

**Ratings come from math, not AI judgment.** Each chunk gets 7 numeric scores. The final rating is a weighted average across all chunks. Pass 2 never re-ranks or overrides numbers — it only handles fields that require whole-book context.

**Content flags are warnings, not themes.** There is an explicit skip list of 50+ common plot/theme words ("betrayal", "dark magic", "prophecy") that are blocked from becoming flags. A flag must be a genuine content warning — something a reader might need to know before picking up the book. Capped at 4 per book to prevent over-flagging.

**Characters need real arc summaries or they're dropped.** "Key character in the narrative" is not an arc summary. If Pass 2 can't describe what a character actually does and how they change, the character is dropped entirely. Fake placeholder data is worse than missing data.

**Non-fiction branches at Pass 2.** A lightweight detection call (Haiku, ~$0.001) classifies the book as fiction or non-fiction before Pass 1. Non-fiction runs an additional Pass 2 call that fills a `non_fiction_info` block with fields like thesis, target audience, structure type, and a sub-type-specific addendum (memoir, self-help, popular science, etc.). The base JSON shape is identical for both types.

---

## Scale and cost

Current cost in `--fast` mode: roughly **$0.10–0.20 per book** (varies by length). As a reference point, a 100,000-word book costs ~$0.10–0.20 and processes in roughly 80–120 seconds. This is not viable for processing thousands of books.

Planned optimizations not yet implemented:
- **Anthropic Batch API** — async processing at 50% cost reduction; suited for bulk ingestion
- **Prompt caching** — the shared prompt scaffolding (master lists, score anchors) is identical across all chunks and could be cached to cut input token costs significantly

The pipeline is designed with these optimizations in mind — the two-pass structure and slim Pass 1 format were chosen in part because they map cleanly onto batch + caching patterns.

---

## Comparing runs

Use `compare.py` to measure quality impact of any change before deploying:

```bash
# Establish baseline
python run_analysis.py book.epub --fast
cp book_analysis.json baseline.json

# Test a change
python run_analysis.py book.epub --fast
python compare.py baseline.json book_analysis.json --detailed
```

Similarity score targets:
- **Same settings, same book:** 90–100
- **Fast mode vs quality mode:** 75+
- **Below 75:** investigate before deploying
