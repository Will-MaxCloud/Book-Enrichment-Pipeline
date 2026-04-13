# Genius Book Analysis Pipeline v2

Automated PDF → structured literary analysis → JSON pipeline.
Designed for consistency, reliability, and mass-scale operation.

## Architecture: Two-Pass Analysis

```
PDF
 │
 ├── Extract (pymupdf)
 │    ├── Text chunks (chapter-aware)
 │    ├── Opening text (~1500 words)
 │    ├── Closing text (~1500 words)
 │    └── PDF metadata (publisher, year)
 │
 ├── PASS 1: Chunk Analysis (per-section)
 │    ├── Themes, characters, humor, tone, violence
 │    ├── Pace, romance, worldbuilding, content flags
 │    └── All scored against FIXED rubric anchors
 │
 ├── PASS 2: Holistic Analysis (whole-book spine)
 │    ├── Sees: opening + ending + chunk summaries
 │    ├── Genre, sub-genre, POV, setting
 │    ├── Sad ending? Cliffhanger? Reading experience?
 │    └── Full-arc character summaries, overall synthesis
 │
 └── AGGREGATE (mathematical, not LLM)
      ├── Weighted-average numeric scores
      ├── Union of content flags
      ├── Merge characters across chunks
      └── Final validated JSON output
```

### Why Two Passes?

Pass 1 alone misses full-book patterns (a joke spread thinly across 300 pages,
a twist ending, overall genre). Pass 2 alone misses granular detail (a single
violent scene in chapter 7, a minor character who appears twice).

Together, they catch everything while keeping each API call focused and within
token limits.

## Output Fields

Every book produces the EXACT same JSON structure:

| Field | Type | Source |
|-------|------|--------|
| metadata.title, author, genre, sub_genres | string/enum | Pass 2 |
| metadata.publisher, publish_year | string/int | PDF metadata |
| themes[].name, prominence, evidence | string/int | Pass 1 (aggregated) + Pass 2 |
| characters[].archetypes, importance | enum[]/int | Pass 1 (aggregated) + Pass 2 |
| setting | object | Pass 2 |
| ratings.tone_light_to_dark | int 1-10 | Pass 1 (weighted avg) |
| ratings.readability | int 1-10 | Computed from complexity |
| ratings.violence | int 1-10 | Pass 1 (weighted avg) |
| ratings.age_target | int 1-10 | Pass 2 (needs full context) |
| ratings.pace | int 1-10 | Pass 1 (weighted avg) |
| ratings.worldbuilding | int 1-10 | Pass 1 (weighted avg) |
| ratings.humor | int 1-10 | Pass 1 (weighted avg) |
| ratings.romance | int 1-10 | Pass 1 (weighted avg) |
| sad_ending | bool | Pass 2 (sees ending text) |
| cliffhanger | bool | Pass 2 (sees ending text) |
| content_flags | enum[] | Pass 1 + Pass 2 (union) |
| pov | enum[] | Pass 2 |
| reading_experience | enum[] | Pass 2 |

## Quick Start

### 1. Install dependencies

```bash
pip install pymupdf anthropic pydantic
```

### 2. Set your API key

```bash
export ANTHROPIC_API_KEY=sk-ant-your-key-here
```

### 3. Run

```bash
# Single book
python run_analysis.py /path/to/book.pdf

# Batch (all PDFs in a folder)
python run_analysis.py /path/to/books/ --batch

# Custom output directory
python run_analysis.py /path/to/book.pdf --output ./results/
```

## Setup with VS Code

1. Open VS Code
2. Open Terminal (Ctrl+` or Cmd+`)
3. Navigate to the project: `cd genius-book-analysis`
4. Create a virtual environment (recommended):
   ```bash
   python -m venv .venv
   source .venv/bin/activate    # macOS/Linux
   .venv\Scripts\activate       # Windows
   ```
5. Install dependencies: `pip install pymupdf anthropic pydantic`
6. Set your API key in a `.env` file or export it
7. Run: `python run_analysis.py yourbook.pdf`

## Setup with Claude Code

Claude Code is Anthropic's CLI tool for agentic coding — it can help you
modify, debug, and extend this pipeline directly from your terminal.

1. Install Claude Code: `npm install -g @anthropic-ai/claude-code`
2. Navigate to the project directory
3. Run `claude` to start an interactive session
4. Ask Claude Code to help with things like:
   - "Add a new rating dimension for emotional_impact"
   - "Make the batch processor run in parallel"
   - "Debug why this PDF isn't extracting chapters correctly"

## Project Structure

```
genius-book-analysis/
├── run_analysis.py     # CLI entry point — orchestrates the pipeline
├── schemas.py          # Pydantic models — the consistency contract
├── extractor.py        # PDF text extraction + chapter segmentation
├── analyzer.py         # AI analysis (Pass 1 + Pass 2) + aggregation
├── example_output.json # What the output looks like
└── README.md           # You are here
```

## Scaling to Mass Operation

The pipeline is stateless per-book. Each `analyze_book()` call is independent.

For mass scale:
- Use a job queue (Redis Queue, Celery, AWS SQS)
- Each worker processes one book at a time
- Results are independent JSON files
- No shared state between analyses
- Monitor API rate limits (add delays between books in batch mode)

## Key Design Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| PDF extraction | pymupdf | Fast, lightweight, handles 95% of book PDFs |
| AI calls | Direct Claude API | Full control, no middleware hiding failures |
| NOT Instructor | — | Instructor silently retries/mutates — bad for consistency |
| NOT Docling | — | Overkill for books, adds instability |
| NOT multi-model | — | Different models have different scoring biases |
| Temperature | 0.0 | Minimize variance across runs |
| Aggregation | Mathematical | Never asks LLM to summarize its own scores |
| Chunking | Chapter-aware + overlap | Preserves narrative units |
| Endings | Pass 2 sees last 1500 words | Never misses the ending |
