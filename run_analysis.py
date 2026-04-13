"""
Genius Book Analysis v4 — Main Pipeline Runner

Modes:
    DEFAULT (quality): Full Pass 1 (Sonnet) + Pass 2 (Sonnet)
    --fast:            Slim Pass 1 (Haiku) + Rich Pass 2 (Sonnet)
                       ~10x cheaper, ~3x faster

Usage:
    python run_analysis.py /path/to/book.pdf
    python run_analysis.py /path/to/book.pdf --fast
    python run_analysis.py /path/to/books/ --batch --fast
    python run_analysis.py /path/to/book.pdf --fast --p1-model claude-haiku-4-5-20251001

Install:
    pip install pymupdf anthropic pydantic

Environment:
    ANTHROPIC_API_KEY=sk-ant-...
"""

from __future__ import annotations
import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

from extractor import extract_text
from analyzer import (
    AnalysisClient, aggregate_analysis, aggregate_analysis_fast,
)
from schemas import BookAnalysis, ChunkAnalysis, SlimChunkAnalysis

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("genius")


def analyze_book(
    pdf_path: str,
    api_key: str,
    output_dir: str | None = None,
    p1_model: str = "claude-sonnet-4-20250514",
    p2_model: str = "claude-sonnet-4-20250514",
    fast_mode: bool = False,
    parallel: bool = False,
) -> BookAnalysis:
    """Full pipeline: PDF → Extract → Pass 1 → Pass 2 → JSON."""
    pdf_path = os.path.abspath(pdf_path)
    pdf_name = Path(pdf_path).stem
    start_time = time.time()

    mode_label = "FAST" if fast_mode else "QUALITY"
    logger.info(f"{'═'*60}")
    logger.info(f"  ANALYZING [{mode_label}]: {pdf_name}")
    logger.info(f"  Pass 1: {p1_model.split('-')[1] if '-' in p1_model else p1_model}")
    logger.info(f"  Pass 2: {p2_model.split('-')[1] if '-' in p2_model else p2_model}")
    logger.info(f"{'═'*60}")

    # ── Step 1: Extract ───────────────────────────────────────────────────
    logger.info("STEP 1/4 · Extracting text from PDF...")
    extraction = extract_text(pdf_path)

    if not extraction.chunks:
        logger.error("No text could be extracted. Is this a scanned PDF?")
        sys.exit(1)

    logger.info(
        f"  → {extraction.total_words:,} words, {extraction.total_pages} pages "
        f"→ {len(extraction.chunks)} chunks"
    )

    book_context = (
        f'Book: "{extraction.title_guess}" by {extraction.author_guess}. '
        f"{extraction.total_words:,} words, {extraction.total_pages} pages."
    )

    client = AnalysisClient(api_key=api_key, model=p1_model, p2_model=p2_model)

    if fast_mode and parallel:
        result = _run_fast_pipeline_parallel(client, extraction, book_context)
    elif fast_mode:
        result = _run_fast_pipeline(client, extraction, book_context)
    else:
        result = _run_quality_pipeline(client, extraction, book_context)

    # ── Save ──────────────────────────────────────────────────────────────
    if output_dir is None:
        output_dir = os.path.dirname(pdf_path) or "."

    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"{pdf_name}_analysis.json")

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result.model_dump(), f, indent=2, ensure_ascii=False, default=str)

    elapsed = time.time() - start_time

    logger.info(f"\n{'═'*60}")
    logger.info(f"  COMPLETE [{mode_label}]: {output_path}")
    logger.info(f"  Time: {elapsed:.1f}s")
    logger.info(f"{'─'*60}")
    logger.info(f"  Title:        {result.metadata.title}")
    logger.info(f"  Author:       {result.metadata.author}")
    logger.info(f"  Genre:        {result.metadata.genre.value}")
    logger.info(f"  Sub-genres:   {', '.join(result.metadata.sub_genres)}")
    logger.info(f"  Categories:   {', '.join(result.categories)}")
    logger.info(f"  Themes:       {len(result.themes)}")
    logger.info(f"  Characters:   {len(result.characters)}")
    logger.info(f"  Tone:         {result.ratings.tone}/10")
    logger.info(f"  Readability:  {result.ratings.readability}/10")
    logger.info(f"  Pace:         {result.ratings.pace}/10")
    logger.info(f"  Humor:        {result.ratings.humor}/10")
    logger.info(f"  Violence:     {result.ratings.violence}/10")
    logger.info(f"  Romance:      {result.ratings.romance}/10")
    logger.info(f"  Worldbuild:   {result.ratings.worldbuilding}/10")
    logger.info(f"  Age target:   {result.ratings.age_target}/10")
    logger.info(f"  Sad ending:   {result.sad_ending}")
    logger.info(f"  Flags:        {', '.join(f.value if hasattr(f, 'value') else str(f) for f in result.content_flags)}")
    logger.info(f"  Read time:    {result.computed_stats.estimated_read_time_hours:.1f}h")
    logger.info(f"{'═'*60}")

    return result


def _run_quality_pipeline(client, extraction, book_context):
    """Full Pass 1 (all fields) + Pass 2."""
    logger.info(f"STEP 2/4 · Pass 1 [QUALITY]: Analyzing {len(extraction.chunks)} chunks...")

    chunk_analyses = []
    failed = []

    for i, chunk in enumerate(extraction.chunks):
        logger.info(f"  [{i+1}/{len(extraction.chunks)}] {chunk.label} ({chunk.word_count:,} words)")
        try:
            analysis = client.analyze_chunk(chunk, book_context)
            logger.info(
                f"         → themes: {len(analysis.themes_detected)}, "
                f"chars: {len(analysis.characters_present)}, "
                f"humor: {analysis.humor_density}/10, violence: {analysis.violence_level}/10")
            chunk_analyses.append(analysis)
        except Exception as e:
            logger.error(f"  ✗ Chunk {i} failed: {e}")
            failed.append(i)

    if not chunk_analyses:
        logger.error("All chunks failed.")
        sys.exit(1)
    if failed:
        logger.warning(f"  {len(failed)} chunks failed — proceeding with {len(chunk_analyses)}")

    logger.info("STEP 3/4 · Pass 2 [QUALITY]: Holistic analysis...")
    holistic = client.analyze_holistic(chunk_analyses, extraction)
    logger.info(f"  → Genre: {holistic.genre.value}")

    logger.info("STEP 4/4 · Aggregating...")
    return aggregate_analysis(chunk_analyses, holistic, extraction)


def _run_fast_pipeline(client, extraction, book_context):
    """Slim Pass 1 (scores only, Haiku) + Rich Pass 2 (Sonnet)."""
    logger.info(f"STEP 2/4 · Pass 1 [FAST]: Scoring {len(extraction.chunks)} chunks...")

    slim_analyses = []
    failed = []

    for i, chunk in enumerate(extraction.chunks):
        logger.info(f"  [{i+1}/{len(extraction.chunks)}] {chunk.label} ({chunk.word_count:,} words)")
        try:
            analysis = client.analyze_chunk_slim(chunk, book_context)
            logger.info(
                f"         → tone:{analysis.tone} pace:{analysis.pace} "
                f"violence:{analysis.violence} chars:{len(analysis.character_names)}")
            slim_analyses.append(analysis)
        except Exception as e:
            logger.error(f"  ✗ Chunk {i} failed: {e}")
            failed.append(i)

    if not slim_analyses:
        logger.error("All chunks failed.")
        sys.exit(1)
    if failed:
        logger.warning(f"  {len(failed)} chunks failed — proceeding with {len(slim_analyses)}")

    logger.info("STEP 3/4 · Pass 2 [FAST → SONNET]: Full analysis...")
    holistic = client.analyze_holistic_fast(slim_analyses, extraction)
    logger.info(f"  → Genre: {holistic.genre.value}")
    logger.info(f"  → Themes: {len(holistic.ranked_themes)}")
    logger.info(f"  → Characters: {len(holistic.ranked_characters)}")

    logger.info("STEP 4/4 · Aggregating...")
    return aggregate_analysis_fast(slim_analyses, holistic, extraction)


def _run_fast_pipeline_parallel(client, extraction, book_context):
    """Slim Pass 1 with 2-concurrent chunk processing, gated by rate limiter."""
    import asyncio
    logger.info(
        f"STEP 2/4 · Pass 1 [FAST/PARALLEL]: Scoring "
        f"{len(extraction.chunks)} chunks (2 concurrent, rate-limited)..."
    )

    slim_analyses, failed = asyncio.run(
        client.analyze_chunks_parallel_fast(extraction.chunks, book_context, max_concurrent=2)
    )

    if not slim_analyses:
        logger.error("All chunks failed.")
        sys.exit(1)
    if failed:
        logger.warning(f"  {len(failed)} chunks failed — proceeding with {len(slim_analyses)}")
    else:
        logger.info(f"  ✓ All {len(slim_analyses)} chunks complete")

    logger.info("STEP 3/4 · Pass 2 [FAST → SONNET]: Full analysis...")
    holistic = client.analyze_holistic_fast(slim_analyses, extraction)
    logger.info(f"  → Genre: {holistic.genre.value}")
    logger.info(f"  → Themes: {len(holistic.ranked_themes)}")
    logger.info(f"  → Characters: {len(holistic.ranked_characters)}")

    logger.info("STEP 4/4 · Aggregating...")
    return aggregate_analysis_fast(slim_analyses, holistic, extraction)


def batch_analyze(
    directory: str,
    api_key: str,
    output_dir: str | None = None,
    p1_model: str = "claude-sonnet-4-20250514",
    p2_model: str = "claude-sonnet-4-20250514",
    fast_mode: bool = False,
    parallel: bool = False,
) -> list[str]:
    pdf_files = sorted(
        list(Path(directory).glob("*.pdf")) +
        list(Path(directory).glob("*.epub"))
    )
    if not pdf_files:
        logger.error(f"No PDF or EPUB files found in {directory}")
        return []

    logger.info(f"Found {len(pdf_files)} books to analyze")
    out = output_dir or str(Path(directory) / "analysis_output")
    results: list[str] = []

    for i, pdf_path in enumerate(pdf_files):
        logger.info(f"\n{'▓'*60}")
        logger.info(f"  BATCH [{i+1}/{len(pdf_files)}]: {pdf_path.name}")
        logger.info(f"{'▓'*60}")
        try:
            analyze_book(str(pdf_path), api_key=api_key, output_dir=out,
                        p1_model=p1_model, p2_model=p2_model,
                        fast_mode=fast_mode, parallel=parallel)
            results.append(pdf_path.name)
        except Exception as e:
            logger.error(f"Failed: {pdf_path.name}: {e}")

        # Brief cooldown between books so the next book starts with headroom
        if i < len(pdf_files) - 1:
            logger.info("  Inter-book cooldown: 15s...")
            time.sleep(15)

    logger.info(f"\nBatch complete: {len(results)}/{len(pdf_files)} succeeded")
    return results


def main():
    parser = argparse.ArgumentParser(
        description="Genius Book Analysis — PDF to structured literary analysis"
    )
    parser.add_argument("input", help="PDF file or directory (with --batch)")
    parser.add_argument("--batch", action="store_true")
    parser.add_argument("--output", "-o", help="Output directory")
    parser.add_argument("--fast", action="store_true",
        help="Fast mode: Haiku for Pass 1, Sonnet for Pass 2 (~10x cheaper)")
    parser.add_argument("--parallel", action="store_true",
        help="Enable 2-concurrent chunk processing in fast mode (requires --fast)")
    parser.add_argument(
        "--p1-model", default=None,
        help="Pass 1 model (default: Sonnet, or Haiku in --fast mode)")
    parser.add_argument(
        "--p2-model", default="claude-sonnet-4-20250514",
        help="Pass 2 model (default: Sonnet, always)")
    parser.add_argument(
        "--model", "-m", default=None,
        help="Set both Pass 1 and Pass 2 to the same model (legacy flag)")
    parser.add_argument(
        "--api-key",
        default=os.environ.get("ANTHROPIC_API_KEY"),
        help="Anthropic API key (or set ANTHROPIC_API_KEY env var)")

    args = parser.parse_args()

    if not args.api_key:
        logger.error("No API key. Set ANTHROPIC_API_KEY or use --api-key")
        sys.exit(1)

    # Resolve models
    if args.model:
        p1_model = args.model
        p2_model = args.model
    elif args.fast:
        p1_model = args.p1_model or "claude-haiku-4-5-20251001"
        p2_model = args.p2_model
    else:
        p1_model = args.p1_model or "claude-sonnet-4-20250514"
        p2_model = args.p2_model

    if args.batch:
        batch_analyze(args.input, args.api_key, args.output,
                     p1_model, p2_model, args.fast, args.parallel)
    else:
        if not os.path.isfile(args.input):
            logger.error(f"File not found: {args.input}")
            sys.exit(1)
        analyze_book(args.input, args.api_key, args.output,
                    p1_model, p2_model, args.fast, args.parallel)


if __name__ == "__main__":
    main()