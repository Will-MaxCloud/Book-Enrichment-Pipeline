"""
Profiler — Per-call and per-stage instrumentation for the analysis pipeline.

Purpose
-------
Capture exactly where time and tokens are spent during a book analysis so we
can make data-driven optimization decisions. Tracks two granularities:

  1. Per API call:    model, input/output tokens, latency, tag (which logical
                      operation it belonged to — e.g. "p1_chunk_5", "p2_holistic")
  2. Per stage:       wall-clock time for each pipeline phase (extraction,
                      pass1, pass2, aggregation, total)

Output
------
A sidecar JSON file (`{book}_profile.json`) next to the analysis result.
For batch runs, an aggregated `batch_profile.json` summarizing all books.

Design
------
Purely observational — does NOT modify any data flowing through the pipeline.
If no profiler is attached to the API client, behavior is exactly unchanged.
Token counts come straight from the Anthropic SDK's `response.usage` object.
"""

from __future__ import annotations
import json
import time
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


@dataclass
class CallRecord:
    """A single API call's measurements."""
    tag: str                 # logical operation, e.g. "p1_chunk_5", "p2_holistic"
    model: str               # model used (e.g. "claude-haiku-4-5-20251001")
    input_tokens: int        # from response.usage.input_tokens (uncached input)
    output_tokens: int       # from response.usage.output_tokens
    latency_seconds: float   # wall-clock time of the call
    timestamp: float         # epoch seconds when the call started
    cache_creation_tokens: int = 0  # tokens written to cache (charged 1.25x)
    cache_read_tokens: int = 0      # tokens read from cache (charged 0.1x)


@dataclass
class StageTiming:
    """Wall-clock duration of a pipeline stage."""
    name: str
    duration_seconds: float


@dataclass
class Profiler:
    """
    Captures per-call API usage and per-stage timings for one book run.

    Lifecycle:
      1. Create:        prof = Profiler()
      2. Attach:        client.attach_profiler(prof)
      3. Wrap stages:   prof.start_stage("pass1"); ...; prof.end_stage()
      4. Finalize:      prof.finalize()
      5. Save/inspect:  prof.to_dict(), prof.save(path), prof.summary_lines()

    The profiler is purely observational — it does not modify any pipeline
    behavior. Removing it (or never attaching it) restores exact original
    behavior.
    """
    book_name: str = ""
    mode: str = ""          # "QUALITY", "FAST", "FASTER" — informational

    calls: list[CallRecord] = field(default_factory=list)
    stages: list[StageTiming] = field(default_factory=list)

    _stage_starts: dict = field(default_factory=dict)
    _book_start: Optional[float] = None
    _book_end: Optional[float] = None

    def __post_init__(self):
        self._book_start = time.time()

    # ── Recording ─────────────────────────────────────────────────────────
    def record_call(self, tag: str, model: str, input_tokens: int,
                    output_tokens: int, latency_seconds: float,
                    timestamp: float, cache_creation_tokens: int = 0,
                    cache_read_tokens: int = 0) -> None:
        """Record one API call's measurements. Called from AnalysisClient._call_api."""
        self.calls.append(CallRecord(
            tag=tag, model=model,
            input_tokens=input_tokens, output_tokens=output_tokens,
            latency_seconds=latency_seconds, timestamp=timestamp,
            cache_creation_tokens=cache_creation_tokens,
            cache_read_tokens=cache_read_tokens,
        ))

    def start_stage(self, name: str) -> None:
        """Mark the start of a pipeline stage (e.g. 'extraction', 'pass1')."""
        self._stage_starts[name] = time.time()

    def end_stage(self, name: str) -> None:
        """Mark the end of a pipeline stage and store its duration."""
        start = self._stage_starts.pop(name, None)
        if start is None:
            return  # never started — silently ignore (defensive)
        self.stages.append(StageTiming(
            name=name, duration_seconds=time.time() - start,
        ))

    def finalize(self) -> None:
        """Close out the profile run. Records the total wall-clock time."""
        self._book_end = time.time()

    # ── Aggregations ──────────────────────────────────────────────────────
    def total_input_tokens(self) -> int:
        return sum(c.input_tokens for c in self.calls)

    def total_output_tokens(self) -> int:
        return sum(c.output_tokens for c in self.calls)

    def total_cache_creation_tokens(self) -> int:
        return sum(c.cache_creation_tokens for c in self.calls)

    def total_cache_read_tokens(self) -> int:
        return sum(c.cache_read_tokens for c in self.calls)

    def total_calls(self) -> int:
        return len(self.calls)

    def total_api_latency(self) -> float:
        """Sum of all individual API call latencies (note: NOT wall-clock if calls are concurrent)."""
        return sum(c.latency_seconds for c in self.calls)

    def total_wall_clock(self) -> float:
        """Wall-clock time from profiler creation to finalize()."""
        end = self._book_end or time.time()
        return end - (self._book_start or end)

    def by_stage(self) -> dict:
        """Group call counts and tokens by stage prefix (everything before the underscore in tag)."""
        groups: dict = defaultdict(lambda: {"calls": 0, "input_tokens": 0,
                                             "output_tokens": 0, "api_latency": 0.0,
                                             "cache_creation_tokens": 0,
                                             "cache_read_tokens": 0})
        for c in self.calls:
            # Tag format: "p1_chunk_5" -> stage "p1"; "p2_holistic" -> "p2"
            stage_key = c.tag.split("_", 1)[0] if "_" in c.tag else c.tag
            g = groups[stage_key]
            g["calls"] += 1
            g["input_tokens"] += c.input_tokens
            g["output_tokens"] += c.output_tokens
            g["api_latency"] += c.latency_seconds
            g["cache_creation_tokens"] += c.cache_creation_tokens
            g["cache_read_tokens"] += c.cache_read_tokens
        return dict(groups)

    def by_model(self) -> dict:
        """Group call counts and tokens by model used."""
        groups: dict = defaultdict(lambda: {"calls": 0, "input_tokens": 0, "output_tokens": 0,
                                             "cache_creation_tokens": 0, "cache_read_tokens": 0})
        for c in self.calls:
            g = groups[c.model]
            g["calls"] += 1
            g["input_tokens"] += c.input_tokens
            g["output_tokens"] += c.output_tokens
            g["cache_creation_tokens"] += c.cache_creation_tokens
            g["cache_read_tokens"] += c.cache_read_tokens
        return dict(groups)

    # ── Output ────────────────────────────────────────────────────────────
    def to_dict(self) -> dict:
        """Serialize to a plain dict suitable for JSON output."""
        return {
            "book_name": self.book_name,
            "mode": self.mode,
            "totals": {
                "wall_clock_seconds": round(self.total_wall_clock(), 2),
                "api_latency_seconds": round(self.total_api_latency(), 2),
                "api_calls": self.total_calls(),
                "input_tokens": self.total_input_tokens(),
                "output_tokens": self.total_output_tokens(),
                "cache_creation_tokens": self.total_cache_creation_tokens(),
                "cache_read_tokens": self.total_cache_read_tokens(),
            },
            "stages": [asdict(s) for s in self.stages],
            "by_stage": self.by_stage(),
            "by_model": self.by_model(),
            "calls": [asdict(c) for c in self.calls],
        }

    def save(self, path: str) -> None:
        """Write the profile to a JSON file."""
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)

    def summary_lines(self) -> list[str]:
        """Compact human-readable summary for logging."""
        d = self.to_dict()
        t = d["totals"]
        lines = [
            f"  Wall clock:       {t['wall_clock_seconds']}s",
            f"  Total API calls:  {t['api_calls']}",
            f"  Input tokens:     {t['input_tokens']:,}",
            f"  Output tokens:    {t['output_tokens']:,}",
        ]
        # Show cache stats only if any caching happened
        if t["cache_creation_tokens"] or t["cache_read_tokens"]:
            lines.append(f"  Cache creation:   {t['cache_creation_tokens']:,} tokens (charged ~1.25x)")
            lines.append(f"  Cache reads:      {t['cache_read_tokens']:,} tokens (charged ~0.1x)")
            # Show effective savings: tokens that would have cost full price but cost 0.1x
            saved = t['cache_read_tokens']
            if saved:
                lines.append(f"  ≈ {saved:,} input tokens served at 10% cost via cache")
        # Per-stage breakdown
        if d["stages"]:
            lines.append(f"  Stage timings:")
            for s in d["stages"]:
                lines.append(f"    {s['name']:14s}  {s['duration_seconds']:.2f}s")
        # Per-stage API breakdown
        if d["by_stage"]:
            lines.append(f"  API by stage (calls / input_tok / output_tok / cache_read):")
            for stage, g in d["by_stage"].items():
                lines.append(f"    {stage:14s}  {g['calls']:>3}  /  "
                             f"{g['input_tokens']:>7,}  /  {g['output_tokens']:>6,}  /  "
                             f"{g.get('cache_read_tokens', 0):>7,}")
        # Per-model breakdown
        if d["by_model"]:
            lines.append(f"  API by model (calls / input_tok / output_tok / cache_read):")
            for model, g in d["by_model"].items():
                # Shorten model name for display
                short = model.replace("claude-", "").rsplit("-", 1)[0]
                lines.append(f"    {short:20s}  {g['calls']:>3}  /  "
                             f"{g['input_tokens']:>7,}  /  {g['output_tokens']:>6,}  /  "
                             f"{g.get('cache_read_tokens', 0):>7,}")
        return lines


def aggregate_batch_profiles(profile_paths: list[str], output_path: str) -> dict:
    """
    Combine multiple per-book profiles into a single batch summary.
    Returns the aggregated dict (also written to output_path).
    """
    books = []
    for p in profile_paths:
        try:
            with open(p, "r", encoding="utf-8") as f:
                books.append(json.load(f))
        except Exception:
            continue  # skip unreadable profiles

    if not books:
        return {}

    # Aggregate totals
    agg = {
        "book_count": len(books),
        "totals": {
            "wall_clock_seconds": round(sum(b["totals"]["wall_clock_seconds"] for b in books), 2),
            "api_latency_seconds": round(sum(b["totals"]["api_latency_seconds"] for b in books), 2),
            "api_calls": sum(b["totals"]["api_calls"] for b in books),
            "input_tokens": sum(b["totals"]["input_tokens"] for b in books),
            "output_tokens": sum(b["totals"]["output_tokens"] for b in books),
            "cache_creation_tokens": sum(b["totals"].get("cache_creation_tokens", 0) for b in books),
            "cache_read_tokens": sum(b["totals"].get("cache_read_tokens", 0) for b in books),
        },
        "averages_per_book": {},
        "by_stage": defaultdict(lambda: {"calls": 0, "input_tokens": 0,
                                          "output_tokens": 0, "api_latency": 0.0,
                                          "cache_creation_tokens": 0, "cache_read_tokens": 0}),
        "by_model": defaultdict(lambda: {"calls": 0, "input_tokens": 0, "output_tokens": 0,
                                          "cache_creation_tokens": 0, "cache_read_tokens": 0}),
        "books": [{"book_name": b.get("book_name", ""), "mode": b.get("mode", ""),
                   "totals": b["totals"]} for b in books],
    }
    # Averages
    n = len(books)
    agg["averages_per_book"] = {
        k: round(v / n, 2) if isinstance(v, float) else round(v / n, 2)
        for k, v in agg["totals"].items()
    }
    # Aggregate by_stage and by_model across all books
    for b in books:
        for stage, g in b.get("by_stage", {}).items():
            agg["by_stage"][stage]["calls"] += g["calls"]
            agg["by_stage"][stage]["input_tokens"] += g["input_tokens"]
            agg["by_stage"][stage]["output_tokens"] += g["output_tokens"]
            agg["by_stage"][stage]["api_latency"] += g.get("api_latency", 0.0)
            agg["by_stage"][stage]["cache_creation_tokens"] += g.get("cache_creation_tokens", 0)
            agg["by_stage"][stage]["cache_read_tokens"] += g.get("cache_read_tokens", 0)
        for model, g in b.get("by_model", {}).items():
            agg["by_model"][model]["calls"] += g["calls"]
            agg["by_model"][model]["input_tokens"] += g["input_tokens"]
            agg["by_model"][model]["output_tokens"] += g["output_tokens"]
            agg["by_model"][model]["cache_creation_tokens"] += g.get("cache_creation_tokens", 0)
            agg["by_model"][model]["cache_read_tokens"] += g.get("cache_read_tokens", 0)

    agg["by_stage"] = dict(agg["by_stage"])
    agg["by_model"] = dict(agg["by_model"])

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(agg, f, indent=2, ensure_ascii=False)
    return agg