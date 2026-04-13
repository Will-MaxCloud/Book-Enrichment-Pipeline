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

## What This Is

An automated pipeline that converts PDF and EPUB books into structured literary analysis JSON. The system reads every word of a book, scores it across multiple dimensions, identifies themes and characters, classifies genre and sub-genre, and produces a standardized data object that powers a book discovery platform.

This is **NOT a summarizer**. It is a **classification and scoring engine** — the data it produces drives search, filtering, recommendations, and discovery. Every field in the output is a filter someone can search by, a tag that connects books to readers, or a rating that builds user trust.

**Reliability is non-negotiable.** Output goes directly into production with no human review step. The system must produce the same output for the same book every time, with variance of ±1 on ratings and zero variance on classifications (genre, themes, categories).

## Running the Pipeline

```bash
# Activate virtual environment first
source .venv/Scripts/activate   # Windows Git Bash
# or: .venv\Scripts\activate    # Windows CMD

# Single book — quality mode (~$1-2, ~10 min)
python run_analysis.py /path/to/book.pdf

# Fast mode — Haiku for Pass 1 (~$0.10-0.20, ~3-5 min)
python run_analysis.py /path/to/book.pdf --fast

# Parallel — fast mode + concurrent chunks (~30-60 sec)
python run_analysis.py /path/to/book.pdf --fast --parallel

# Batch process a directory
python run_analysis.py /path/to/books/ --batch --fast --parallel

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

### Pass 1 — The Detective (per-chunk)

Reads every word of the book chunk by chunk. In fast mode, captures only:
- 7 numeric ratings (tone, readability, violence, pace, worldbuilding, humor, romance) scored 1–10
- Character names detected (just names, no analysis)
- Content flags detected
- One-sentence summary of each section

Pass 1's job is to **record evidence** — not make judgments about what matters most. Ratings are mathematically averaged across all chunks to produce stable, reproducible scores.

### Pass 2 — The Judge (single call, always Sonnet)

Receives opening text (~1500 words), closing text (~1500 words), and all chunk summaries/scores. Handles ALL intelligent classification:
- Genre, sub-genres (from master lists)
- Themes (top 10 from master list, ranked by importance)
- Categories (exactly 3 from master list)
- Characters (top 8, with arcs, genders, archetypes, ages)
- Setting, reading experience, POV, humor types
- Content flags (top 4 most significant)
- Sad ending, cliffhanger, overall summary

Pass 2 sees the full picture and decides what matters.

### Why the Split Exists

Early versions had Pass 1 doing everything per chunk. This caused:
- **Theme inconsistency:** Different chunks detected the same theme with slightly different wording; averaging produced noise.
- **Character ranking instability:** A minor character scoring importance 8 in their one scene looked as important as the protagonist averaging 7 across 30 chunks.
- **High cost:** Full prompt = ~1000 output tokens per chunk. Slim prompt = ~100–150 tokens (10x cheaper).

Pass 1 handles what needs chunk-by-chunk measurement (ratings). Pass 2 handles what needs whole-book judgment (classifications). Neither works alone.

## Data Priority

### Tier 1 — Discovery Engine (must be perfect)
- **Genre and sub-genre:** Primary search/filter. From controlled master lists only.
- **Themes (top 10):** Secondary discovery tags. From master list of ~70 themes. Pass 2 ranks them.
- **Categories (exactly 3):** Tertiary tags covering vibes, tropes, settings, narrative style. From master list of ~100+ categories.
- **Ratings (1–10):** Trust engine — tone, readability, violence, pace, worldbuilding, humor, romance, age_target. Come from mathematically averaging Pass 1 chunk scores.

### Tier 2 — Display Data
- Overall summary, reading experience, setting (all controlled enums), sad ending / cliffhanger booleans.

### Tier 3 — Safety Filter
- **Content flags:** Capped at 4 maximum. Must be genuine content warnings, not themes or plot elements. Split between `sexual_content` (consensual) and `sexual_violence` (assault) — these are fundamentally different warnings.

### Tier 4 — Future Use
- **Characters (top 8):** Name, gender, importance, archetypes, age category, arc summary. Future character preview feature.

## Key Files

| File | Purpose |
|------|---------|
| `run_analysis.py` | CLI entry point, pipeline orchestration, `analyze_book()` |
| `analyzer.py` | `AnalysisClient` class — all Claude API calls, prompt builders, sanitization/mapping tables, aggregation logic |
| `schemas.py` | All Pydantic models, enums, and master taxonomy lists |
| `extractor.py` | PDF/EPUB text extraction, chapter detection, chunking |
| `compare.py` | Comparison tool for tracking quality across runs and configurations |

## Controlled Vocabularies

Every classification field uses a controlled vocabulary with sanitization mapping tables in `analyzer.py` (`_sanitize_chunk_data`, `_sanitize_holistic_data`). The AI can return anything — creative synonyms, variations, misspellings — and the mapping tables normalize to the correct enum value. If something doesn't map, it is **dropped**, not passed through as garbage.

Key mapping tables and their scale:
- **Content flags:** 150+ mappings + 50+ explicit skip entries for non-warnings (e.g., "betrayal", "dark_magic", "prophecy")
- **Character archetypes:** 50+ mappings (e.g., "king" → `royal`, "spy" → `trickster`)
- **Time periods:** 60+ mappings (e.g., "Victorian" → `19th_century`)
- **Setting types:** 60+ mappings (e.g., "fantasy world" → `fantasy_realm`)
- **Reading experience:** 30+ mappings (e.g., "gripping" → `tense`, "quirky" → `whimsical`)
- **Genres, themes, sub-genres, categories:** Fuzzy matching against master lists (60%+ word overlap = match)

All list outputs (sub-genres, themes, categories, flags) are deduplicated with seen-sets before final output.

Master lists and enums live in `schemas.py`:
- `MASTER_THEMES` — 70+ canonical theme strings
- `MASTER_SUB_GENRES` — 200+ sub-genre strings
- `MASTER_CATEGORIES` — 100+ category strings
- `Genre` enum — 41 genres
- `CharacterArchetype` enum — 21 archetypes
- `ContentFlag` enum — 15+ flags
- `SCORE_ANCHORS` — book-example anchors for every 1–10 rating scale

When modifying prompts or adding new dimensions, always reference these master lists.

## Score Anchors (Rating Calibration)

Every 1–10 rating is anchored to concrete book examples to prevent drift:

| Rating | Tone | Violence | Readability | Pace | Humor | Romance | Worldbuilding | Age target |
|--------|------|----------|-------------|------|-------|---------|---------------|------------|
| 1 | Winnie the Pooh | Paddington | Ulysses | Proust | The Road | Lord of the Flies | Contemporary realism | Goodnight Moon |
| 5 | Hunger Games | Percy Jackson | Standard literary fiction | Most literary fiction | Harry Potter | Hunger Games | Distinct setting | Hunger Games |
| 10 | Blood Meridian | American Psycho | Diary of a Wimpy Kid | Jack Reacher | Discworld | The Notebook | Lord of the Rings / Dune | A Little Life |

## Character Handling

Characters have been the most difficult field to get right. Key rules:

- **No fake arc summaries.** If Pass 2 can't provide a real arc summary, the character is dropped entirely. "Key character in the narrative" placeholder is poisoning the data.
- **Importance from chunk appearance ratio**, not rank position. >50% of chunks = importance 10. This is mathematically stable across runs.
- **Pass 2 controls who makes the cut** (top 8), but the importance number comes from chunk data.
- **Gender is explicitly stated** in both Pass 1 and Pass 2 prompts — not inferred.
- **Nickname deduplication** — substring matching merges "Haflea" and "Flea" into one entry, keeping the longer name.
- **Generic names blocked** — "the stranger", "the girl", "narrator", etc. are filtered out. Only named characters survive.
- **Archetypes and ages from Pass 2** — in fast mode, Pass 2 provides `character_archetypes` and `character_ages` dicts since Pass 1 doesn't collect them.

## Content Flag Philosophy

Content flags are **content warnings**, not themes, not plot elements, not setting descriptors:

- "Betrayal" → THEME → skip
- "Dark magic" → SETTING ELEMENT → skip
- "Graphic violence" → CONTENT WARNING → include
- "Sexual assault" → CONTENT WARNING → include (distinct from `sexual_content`)

The mapping table explicitly skips 50+ common theme/plot words. Output is capped at 4 flags to prevent over-flagging.

## Design Principles

1. **Controlled vocabularies over free text.** Every classification comes from a master list. The AI picks from the list; the mapping table catches anything that doesn't match.
2. **Mathematical stability over AI judgment for numbers.** Ratings are weighted averages of chunk scores. No AI reranking of numbers.
3. **AI judgment for classifications.** Genre, themes, character ranking — these need whole-book context that only Pass 2 has.
4. **Fail safe, not fail open.** If a character has no arc summary, drop it. If a theme doesn't match the master list, drop it. Never pass garbage through.
5. **Same input = same output.** Temperature 0.0, deterministic aggregation, controlled vocabularies. Only acceptable variance is ±1 on ratings.

## Testing Protocol

Use `compare.py` to track quality across changes:

```bash
# Establish baseline
python run_analysis.py book.pdf --fast
cp book_analysis.json baseline.json

# Test a change
python run_analysis.py book.pdf --fast
python compare.py baseline.json book_analysis.json --detailed
```

Similarity score targets:
- **Sonnet vs Sonnet (same settings):** 90–100
- **Fast mode vs quality mode:** 75+
- **Any change below 75:** investigate before deploying

## Known Limitations

- **Chapter-heavy books** produce more chunks (one per chapter). A 38-chapter book = 38 API calls regardless of chunk size settings, because chapter boundaries take priority over windowed chunking.
- **Haiku cannot do full literary analysis.** When tested for the full pipeline, it scored 56/100 similarity to Sonnet — dropping the main character entirely. Haiku is only viable for the slim scoring task (7 numbers + names + flags).
- **Parallel processing:** Max 2 concurrent with 1s stagger. Higher concurrency causes 429 errors on standard API tiers.
- **Scanned PDFs** (image-based) extract no text. The system detects this and exits with a warning.

## Output Schema Reference

```
metadata:        {title, author, book_type, genre, sub_genres[], publisher?, publish_year?}
themes:          [{name, prominence (1-10)}]          — max 10, sorted by prominence
categories:      [string]                              — exactly 3, from master list
characters:      [{name, role, importance, gender, archetypes[], arc_summary, age_category}] — max 8
setting:         {primary_location, time_period, setting_type, real_or_fictional, additional_locations[]}
ratings:         {tone, readability, violence, age_target, pace, worldbuilding, humor, romance} — all 1-10
computed_stats:  {total_words, total_pages, avg_sentence_length, avg_words_per_page, estimated_read_time_hours}
humor:           {humor_density (1-10), primary_humor_types[]}
pov:             [pov_types]
reading_experience: [enum values]                     — max 4
sad_ending:      bool
cliffhanger:     bool
content_flags:   [enum values]                        — max 4
overall_summary: string
analysis_version: "4.0.0"
```
