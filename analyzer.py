"""
Genius Book Analysis v3.2 — Full taxonomy + character arc fix.

KEY CHANGES:
- Categories: new field, top 3 per book from master list
- Character arcs: Pass 2 gets explicit character names to write arcs for.
  Characters without arcs get DROPPED, not given fake "Key character" text.
- Genre: updated mapping for new genre enum values
- All iron wall mapping tables from v3.1 preserved
"""

from __future__ import annotations
import json
import logging
import time
from typing import Any

from schemas import (
    SCORE_ANCHORS, MASTER_THEMES, MASTER_THEMES_SET,
    MASTER_SUB_GENRES, MASTER_SUB_GENRES_SET,
    MASTER_CATEGORIES, MASTER_CATEGORIES_SET,
    ChunkAnalysis, SlimChunkAnalysis, HolisticAnalysis, BookAnalysis,
    BookMetadata, Theme, HumorProfile, Character,
    SettingInfo, ContentRatings, ComputedStats,
    HumorType, ContentFlag, POVType, ReadingExperience,
    CharacterArchetype, AgeCategory, BookType, Genre, TimePeriod, SettingType,
    # Non-fiction enums (Session A: schema infrastructure)
    BookSubType, TargetAudience, StructureType, ToneRegister, ConclusionType,
    CommitmentLevel, NarrativeShape, SubjectRelationship, HistoricalPerspective,
    AcademicLevel, PhilosophyFocus, BusinessAudienceRole, HealthEvidenceBasis,
    RecipeDifficulty, CookbookPurpose, TravelStyle, TrueCrimeCaseType,
    TrueCrimeResolution, TrueCrimePerspective,
    # Non-fiction models (used by Session C sanitizer)
    NonFictionInfo, SelfHelpAddendum, MemoirBiographyAddendum,
    HistoryNarrativeAddendum, AcademicTextbookAddendum, PopularScienceAddendum,
    PhilosophyReligionAddendum, BusinessEconomicsAddendum, HealthFitnessAddendum,
    CookingFoodAddendum, TravelNatureAddendum, TrueCrimeAddendum,
)
from extractor import TextChunk, ExtractionResult
from ratelimiter import RollingRateLimiter

logger = logging.getLogger(__name__)


# Tier-1 default rate limits per model. Anthropic enforces separate buckets
# per model, so each gets its own RollingRateLimiter instance.
# The 90% threshold in RollingRateLimiter gives natural margin under these.
# Override via AnalysisClient(rate_limits={...}) for higher tiers.
_DEFAULT_RATE_LIMITS: dict[str, dict[str, int]] = {
    "claude-haiku-4-5-20251001": {"rpm": 50, "tpm": 50_000},
    "claude-sonnet-4-20250514":  {"rpm": 50, "tpm": 30_000},
}


# ═══════════════════════════════════════════════════════════════════════════════
# PROMPT BUILDERS
# ═══════════════════════════════════════════════════════════════════════════════

def _format_anchors(key: str) -> str:
    return "\n".join(f"  {s} = {d}" for s, d in SCORE_ANCHORS[key].items())

def _build_chunk_prompt(chunk: TextChunk, book_context: str) -> str:
    theme_list = "\n".join(f'  - "{t}"' for t in MASTER_THEMES)
    archetype_values = ", ".join(f'"{a.value}"' for a in CharacterArchetype)
    humor_values = ", ".join(f'"{h.value}"' for h in HumorType)
    flag_values = ", ".join(f'"{f.value}"' for f in ContentFlag)

    return f"""You are a literary analyst. Respond ONLY with valid JSON. No preamble, no markdown.

CONTEXT: {book_context}
SECTION: {chunk.label} (pages {chunk.page_start}-{chunk.page_end}, ~{chunk.word_count} words)

═══ SCORING RUBRICS ═══
TONE (1-10):
{_format_anchors("tone")}
READABILITY (1-10):
{_format_anchors("readability")}
VIOLENCE (1-10):
{_format_anchors("violence")}
PACE (1-10):
{_format_anchors("pace")}
WORLDBUILDING (1-10):
{_format_anchors("worldbuilding")}
HUMOR (1-10):
{_format_anchors("humor")}
ROMANCE (1-10):
{_format_anchors("romance")}
CHARACTER IMPORTANCE (1-10):
{_format_anchors("character_importance")}
THEME PROMINENCE (1-10):
{_format_anchors("prominence")}

═══ MASTER THEME LIST — pick from this list ONLY ═══
{theme_list}

═══ CONTENT FLAGS (content warnings ONLY) ═══
{flag_values}
"sexual_content" = consensual. "sexual_violence" = assault. These are DIFFERENT.

═══ REQUIRED JSON ═══
{{
  "chunk_index": {chunk.index},
  "chunk_label": "{chunk.label}",
  "word_count": {chunk.word_count},
  "themes_detected": ["theme from master list"],
  "theme_prominences": {{"theme": int_1_to_10}},
  "characters_present": [
    {{
      "name": "character's ACTUAL name (not generic descriptions)",
      "importance": int_1_to_10,
      "gender": "male|female|non-binary|unknown",
      "archetypes": [{archetype_values}],
      "arc_summary": "1-2 sentences about what this character DOES in this section. NEVER leave empty.",
      "age_category": "child|teen|young_adult|adult|elderly|ageless|null"
    }}
  ],
  "humor_density": int_1_to_10,
  "humor_types": [{humor_values}],
  "tone": int_1_to_10,
  "readability_score": int_1_to_10,
  "violence_level": int_1_to_10,
  "pace_score": int_1_to_10,
  "romance_level": int_1_to_10,
  "worldbuilding_level": int_1_to_10,
  "content_flags": [{flag_values}],
  "notable_observations": "string"
}}

RULES:
- 2-8 themes from MASTER LIST ONLY.
- Only named characters. Every character MUST have a real arc_summary (not empty, not generic).
- Content flags are warnings only — not themes. ONLY flag content that is
  ACTUALLY PRESENT in this section (not just hinted at). A passing mention
  in one sentence is NOT enough — the content must be genuinely shown or
  discussed at meaningful length.

TEXT:
---
{chunk.text[:100000]}
---

JSON:"""


def _build_holistic_prompt(chunk_analyses, extraction):
    chunk_summaries = []
    for ca in chunk_analyses:
        themes_str = ", ".join(ca.themes_detected[:4])
        chars_str = ", ".join(c.name for c in ca.characters_present[:3])
        chunk_summaries.append(
            f"[{ca.chunk_label}] Themes: {themes_str}. Chars: {chars_str}. "
            f"Tone:{ca.tone} Violence:{ca.violence_level} Humor:{ca.humor_density} Pace:{ca.pace_score}. "
            f"Notes: {ca.notable_observations}"
        )

    chunk_spine = "\n".join(chunk_summaries)
    theme_summary = json.dumps(_aggregate_themes_for_prompt(chunk_analyses), indent=2)

    # Get merged character list with explicit names for arc generation
    char_data = _aggregate_characters_for_prompt(chunk_analyses)
    char_summary = json.dumps(char_data, indent=2)
    char_names = [c["name"] for c in char_data[:15]]
    char_names_str = ", ".join(f'"{n}"' for n in char_names)

    # Get all detected theme names for ranking
    theme_data_for_ranking = _aggregate_themes_for_prompt(chunk_analyses)
    theme_names = [t["name"] for t in theme_data_for_ranking[:20]]
    theme_names_str = ", ".join(f'"{t}"' for t in theme_names)

    genre_values = ", ".join(f'"{g.value}"' for g in Genre)
    book_type_values = ", ".join(f'"{b.value}"' for b in BookType)
    pov_values = ", ".join(f'"{p.value}"' for p in POVType)
    exp_values = ", ".join(f'"{e.value}"' for e in ReadingExperience)
    flag_values = ", ".join(f'"{f.value}"' for f in ContentFlag)
    humor_values = ", ".join(f'"{h.value}"' for h in HumorType)
    time_values = ", ".join(f'"{t.value}"' for t in TimePeriod)
    setting_values = ", ".join(f'"{s.value}"' for s in SettingType)
    category_list = "\n".join(f'  - "{c}"' for c in MASTER_CATEGORIES)

    return f"""You are performing a HOLISTIC analysis of a complete book.
Respond ONLY with valid JSON. No preamble, no markdown.

BOOK: "{extraction.title_guess}" by {extraction.author_guess}
TOTAL: {extraction.total_words:,} words, {extraction.total_pages} pages

═══ AGE TARGET RUBRIC ═══
{_format_anchors("age_target")}

═══ OPENING TEXT ═══
{extraction.opening_text[:6000]}

═══ CLOSING TEXT ═══
{extraction.closing_text[:6000]}

═══ SECTION SPINE ═══
{chunk_spine}

═══ THEME DATA ═══
{theme_summary}

═══ CHARACTER DATA ═══
{char_summary}

═══ CATEGORY LIST — pick exactly 3 that best describe this book ═══
{category_list}

═══ REQUIRED JSON ═══
{{
  "book_type": one of [{book_type_values}],
  "genre": one of [{genre_values}],
  "sub_genres": ["1-4 sub-genre labels"],
  "title": "string",
  "author": "string",
  "pov_types": [{pov_values}],
  "pov_notes": "string",
  "setting": {{
    "primary_location": "string",
    "time_period": one of [{time_values}],
    "setting_type": one of [{setting_values}],
    "real_or_fictional": "real|fictional|mixed",
    "additional_locations": ["string"]
  }},
  "reading_experience": ["1-4 from: {exp_values}"],
  "categories": ["exactly 3 from the category list above"],
  "sad_ending": true|false,
  "cliffhanger": true|false,
  "ending_notes": "string",
  "age_target": int_1_to_10,
  "content_flags": [{flag_values}],
  "overall_summary": "3-5 sentence synthesis",
  "character_arcs": {{
    "CharacterName": "1-3 sentence arc summary covering their FULL journey in the book"
  }},
  "character_genders": {{
    "CharacterName": "male|female|non-binary|unknown"
  }},
  "ranked_themes": ["top 10 themes from detected list, ranked MOST important first"],
  "ranked_characters": ["top 8 character names, ranked MOST important first"],
  "humor_types": ["1-3 from: {humor_values}"]
}}

CRITICAL RULES:
- genre and book_type must be SINGLE string values, not arrays.
- You MUST provide character_arcs AND character_genders entries for EACH of these characters: [{char_names_str}].
  Use the EXACT names listed. Each arc must be 1-3 real sentences, never empty or generic.
- ranked_themes: From the detected themes [{theme_names_str}], pick the TOP 10 that are
  truly CORE to this book. Rank most-important first. Only include themes genuinely central
  to the work. If fewer than 10 are truly core, include fewer.
- ranked_characters: Pick the TOP 8 most important characters. Rank by overall importance
  to the story — protagonist first, then major characters. Do NOT include minor characters
  who only appear briefly or in a few scenes.
- categories must be exactly 3 items from the category list.
- sub_genres: Each sub-genre must be UNIQUE — no duplicates. 1-4 distinct sub-genres.
- Pay attention to CLOSING TEXT for sad_ending and cliffhanger.

JSON:"""


# ═══════════════════════════════════════════════════════════════════════════════
# API CLIENT
# ═══════════════════════════════════════════════════════════════════════════════

class AnalysisClient:
    def __init__(self, api_key, model="claude-sonnet-4-20250514",
                 p2_model=None, max_retries=3, retry_delay=2.0,
                 detection_model="claude-haiku-4-5-20251001",
                 rate_limits: dict | None = None):
        """
        Args:
            detection_model: model used for the cheap fiction/non-fiction
                             pre-classification call. Defaults to Haiku
                             regardless of Pass 1/2 model choice — detection
                             is a simple binary task that doesn't need Sonnet.
            rate_limits: optional override of per-model {rpm, tpm} dict.
                         Defaults to _DEFAULT_RATE_LIMITS (Tier 1). Pass
                         e.g. {"claude-haiku-4-5-20251001": {"rpm": 100, "tpm": 100_000}}
                         to raise limits for a higher tier.

        Rate limiting is enforced by RollingRateLimiter (sliding 60s window,
        90% safety threshold, per-model bucket). The 429 retry block below
        remains as a safety net but should rarely fire under normal load.
        """
        self.api_key = api_key
        self.model = model
        self.p2_model = p2_model or model
        self.detection_model = detection_model
        self.max_retries, self.retry_delay = max_retries, retry_delay
        self._profiler = None  # optional Profiler instance; see attach_profiler()

        # ── Per-model rate limiters (lazy-created in _get_limiter) ────────
        self._rate_limits = rate_limits or _DEFAULT_RATE_LIMITS
        self._limiters: dict[str, RollingRateLimiter] = {}

    def attach_profiler(self, profiler) -> None:
        """
        Attach a Profiler to record per-call token usage and latency.
        Optional — if never attached, _call_api behaves exactly as before.
        """
        self._profiler = profiler

    def _get_limiter(self, model: str) -> RollingRateLimiter:
        """Return (and lazily create) the rate limiter for a given model."""
        if model not in self._limiters:
            # Unknown models fall back to conservative Sonnet-tier limits.
            cfg = self._rate_limits.get(model, {"rpm": 50, "tpm": 30_000})
            self._limiters[model] = RollingRateLimiter(
                rpm_limit=cfg["rpm"], tpm_limit=cfg["tpm"])
        return self._limiters[model]

    def _call_api(self, prompt, max_tokens=4096, model_override=None, tag="untagged"):
        import anthropic
        client = anthropic.Anthropic(api_key=self.api_key)
        use_model = model_override or self.model

        # Pre-emptive rate limiting (per-model bucket, 90% safety threshold).
        # Estimate input tokens from prompt length conservatively (~3 chars/tok).
        # The estimate slightly over-counts for English, which keeps us safe.
        estimated_input = max(len(prompt) // 3, 100)
        self._get_limiter(use_model).wait_if_needed(estimated_input)

        for attempt in range(1, self.max_retries + 1):
            try:
                call_start = time.time()
                r = client.messages.create(
                    model=use_model, max_tokens=max_tokens, temperature=0.0,
                    messages=[{"role": "user", "content": prompt}])

                # Profiler hook — only runs if attached. Defensive: any failure
                # here is silently swallowed so profiling can never break a run.
                if self._profiler is not None:
                    try:
                        self._profiler.record_call(
                            tag=tag, model=use_model,
                            input_tokens=r.usage.input_tokens,
                            output_tokens=r.usage.output_tokens,
                            latency_seconds=time.time() - call_start,
                            timestamp=call_start,
                        )
                    except Exception as e:
                        logger.warning(f"Profiler record failed (ignored): {e}")
                return "".join(b.text for b in r.content if b.type == "text").strip()
            except anthropic.RateLimitError:
                # Limiter under-estimated — fall back to backoff retry.
                w = self.retry_delay * (2 ** (attempt - 1))
                logger.warning(f"Rate limited — {w:.1f}s (attempt {attempt})")
                time.sleep(w)
            except anthropic.APIError as e:
                logger.error(f"API error attempt {attempt}: {e}")
                if attempt == self.max_retries: raise
                time.sleep(self.retry_delay)
        raise RuntimeError(f"Failed after {self.max_retries} retries")

    # ── Full mode ─────────────────────────────────────────────────────────
    def analyze_chunk(self, chunk, book_context, tag="p1_chunk"):
        return ChunkAnalysis(**_sanitize_chunk_data(
            _safe_parse_json(self._call_api(
                _build_chunk_prompt(chunk, book_context), tag=tag))))

    def analyze_holistic(self, chunk_analyses, extraction):
        return HolisticAnalysis(**_sanitize_holistic_data(
            _safe_parse_json(self._call_api(
                _build_holistic_prompt(chunk_analyses, extraction), 4096,
                model_override=self.p2_model, tag="p2_holistic"))))

    # ── Fast mode ─────────────────────────────────────────────────────────
    def analyze_chunk_slim(self, chunk, book_context, tag="p1_chunk", book_type="fiction"):
        return SlimChunkAnalysis(**_sanitize_slim_chunk_data(
            _safe_parse_json(self._call_api(
                _build_slim_chunk_prompt(chunk, book_context, book_type), 1024, tag=tag))))

    def analyze_holistic_fast(self, slim_analyses, extraction, p2_max_tokens=4096):
        return HolisticAnalysis(**_sanitize_holistic_data(
            _safe_parse_json(self._call_api(
                _build_fast_holistic_prompt(slim_analyses, extraction), p2_max_tokens,
                model_override=self.p2_model, tag="p2_holistic"))))

    # ── Detection (Session B) ─────────────────────────────────────────────
    def detect_book_type(self, extraction) -> str:
        """
        Classify the book as 'fiction' or 'non_fiction'.

        Uses a small Haiku call with the opening sample only. Opening alone
        is sufficient for >99% reliability — fiction immediately shows narrative
        voice/dialogue/scene-setting; non-fiction shows expository prose,
        introductory framing, and often headings or citations.

        Returns:
            'fiction' or 'non_fiction'. On any error or ambiguous result,
            defaults to 'fiction' (the safer/legacy path).

        Cost: ~$0.001 per call (~800 words opening sample).
        """
        # Use ~800 words of opening for the decision. The model returns one word.
        opening = (extraction.opening_text or "")[:5000]  # ~800 words = ~5000 chars
        if not opening.strip():
            logger.warning("  [detection] No opening text available — defaulting to 'fiction'")
            return "fiction"

        prompt = _build_detection_prompt(opening)
        try:
            response = self._call_api(prompt, max_tokens=10,
                                       model_override=self.detection_model,
                                       tag="detection")
            normalized = response.lower().strip().strip('"').strip("'")
            # The model might say "non-fiction" or "non fiction" — normalize
            normalized = normalized.replace("-", "_").replace(" ", "_")
            if "non_fiction" in normalized or "nonfiction" in normalized:
                return "non_fiction"
            elif "fiction" in normalized:
                return "fiction"
            else:
                logger.warning(f"  [detection] Ambiguous response {response!r} — defaulting to 'fiction'")
                return "fiction"
        except Exception as e:
            logger.warning(f"  [detection] Detection call failed ({e}) — defaulting to 'fiction'")
            return "fiction"

    # ── Non-fiction Pass 2 (Session C) ────────────────────────────────────
    def analyze_holistic_nonfic(self, pass1_analyses, extraction) -> NonFictionInfo:
        """
        Run the non-fiction Pass 2 prompt and return a populated NonFictionInfo
        with the 7 common-core fields filled in (no addendums yet — Session E).

        Uses self.detection_model (Haiku) because:
          - The task is mostly classification (enum picks) plus one short
            descriptive field (thesis). Haiku handles this well.
          - Cost is ~$0.005 vs ~$0.018 if we used Sonnet.
          - Output is small (~500 tokens), so Haiku's lower output speed
            doesn't matter much.

        On failure, returns a fallback NonFictionInfo built via
        make_stub_nonfic_info() so the caller never has to handle exceptions.
        The thesis will clearly indicate the analysis failed.
        """
        try:
            prompt = _build_nonfic_holistic_prompt(pass1_analyses, extraction)
            response = self._call_api(prompt, max_tokens=1024,
                                       model_override=self.detection_model,
                                       tag="p2_nonfic")
            parsed = _safe_parse_json(response)
            sanitized = _sanitize_nonfic_data(parsed)
            return NonFictionInfo(**sanitized)
        except Exception as e:
            logger.warning(f"  [non-fic] Pass 2 failed ({e}) — using fallback stub")
            # Build a fallback that signals failure in the output
            fallback = make_stub_nonfic_info()
            fallback.thesis = f"[Pass 2 failed: {type(e).__name__}]"
            return fallback

    # ── Unified non-fiction Pass 2 (Session D) ────────────────────────────
    def analyze_holistic_nonfic_full(self, pass1_analyses, extraction,
                                      p2_max_tokens=4096):
        """
        Unified non-fiction Pass 2: ONE API call that returns both the universal
        book fields (genre, themes, categories, setting, summary, etc.) AND the
        NonFictionInfo block (thesis, target_audience, sub_type, etc.).

        Replaces the need to run the fiction Pass 2 on non-fiction books.

        Returns:
            tuple of (HolisticAnalysis, NonFictionInfo)

        Model choice: uses self.p2_model (matches user's quality vs fast vs
        faster mode). Sonnet handles thematic synthesis and summary writing
        better than Haiku, and we're doing significantly more work in this
        one call than in Session C's NonFictionInfo-only version.

        Cost (Sonnet): ~10K input + ~2K output ≈ $0.030 per book.
        Same order of magnitude as the fiction Pass 2 it replaces.

        On failure, returns fallback objects so the caller never has to handle
        exceptions. The fallback HolisticAnalysis has minimal-default fields;
        the fallback NonFictionInfo has its thesis set to indicate the failure.
        """
        try:
            prompt = _build_nonfic_holistic_prompt(pass1_analyses, extraction)
            response = self._call_api(prompt, max_tokens=p2_max_tokens,
                                       model_override=self.p2_model,
                                       tag="p2_nonfic")
            parsed = _safe_parse_json(response)

            # Build NonFictionInfo from the NonFictionInfo-shaped fields
            nfi_data = _sanitize_nonfic_data(parsed)
            nfi = NonFictionInfo(**nfi_data)

            # Build HolisticAnalysis from the universal-shaped fields
            universal_data = _sanitize_nonfic_universal_data(parsed, extraction)
            holistic = HolisticAnalysis(**universal_data)

            return holistic, nfi
        except Exception as e:
            logger.warning(f"  [non-fic] Pass 2 (full) failed ({e}) — using fallbacks")
            # NonFictionInfo fallback: clearly mark the failure
            nfi_fallback = make_stub_nonfic_info()
            nfi_fallback.thesis = f"[Pass 2 failed: {type(e).__name__}]"
            # HolisticAnalysis fallback: minimal defaults via universal sanitizer
            # (empty dict gives all-default output that still constructs validly)
            universal_fallback = _sanitize_nonfic_universal_data({}, extraction)
            holistic_fallback = HolisticAnalysis(**universal_fallback)
            return holistic_fallback, nfi_fallback


# ═══════════════════════════════════════════════════════════════════════════════
# VALID SETS
# ═══════════════════════════════════════════════════════════════════════════════

_VALID_FLAGS = {f.value for f in ContentFlag}
_VALID_HUMOR = {h.value for h in HumorType}
_VALID_ARCH = {a.value for a in CharacterArchetype}
_VALID_POV = {p.value for p in POVType}
_VALID_EXP = {r.value for r in ReadingExperience}
_VALID_GENRES = {g.value for g in Genre}
_VALID_BTYPES = {b.value for b in BookType}
_VALID_TIME = {t.value for t in TimePeriod}
_VALID_SETTING = {s.value for s in SettingType}
_VALID_AGE = {a.value for a in AgeCategory}

# ── Non-fiction valid-sets (Session A infrastructure) ──────────────────────────
# Common core
_VALID_SUBTYPE = {s.value for s in BookSubType}
_VALID_AUDIENCE = {a.value for a in TargetAudience}
_VALID_STRUCTURE = {s.value for s in StructureType}
_VALID_TONE_REG = {t.value for t in ToneRegister}
_VALID_CONCLUSION = {c.value for c in ConclusionType}
# Addendum-specific
_VALID_COMMITMENT = {c.value for c in CommitmentLevel}
_VALID_NARRATIVE = {n.value for n in NarrativeShape}
_VALID_SUBJECT_REL = {s.value for s in SubjectRelationship}
_VALID_HIST_PERSP = {h.value for h in HistoricalPerspective}
_VALID_ACAD_LEVEL = {a.value for a in AcademicLevel}
_VALID_PHIL_FOCUS = {p.value for p in PhilosophyFocus}
_VALID_BIZ_ROLE = {b.value for b in BusinessAudienceRole}
_VALID_HEALTH_BASIS = {h.value for h in HealthEvidenceBasis}
_VALID_RECIPE_DIFF = {r.value for r in RecipeDifficulty}
_VALID_COOKBOOK_PURPOSE = {c.value for c in CookbookPurpose}
_VALID_TRAVEL_STYLE = {t.value for t in TravelStyle}
_VALID_TC_CASE = {t.value for t in TrueCrimeCaseType}
_VALID_TC_RESOLUTION = {t.value for t in TrueCrimeResolution}
_VALID_TC_PERSPECTIVE = {t.value for t in TrueCrimePerspective}


# ═══════════════════════════════════════════════════════════════════════════════
# LANGUAGE RESOLUTION
# ═══════════════════════════════════════════════════════════════════════════════

_LANGUAGE_MAP = {
    "en": "English", "eng": "English",
    "it": "Italian", "ita": "Italian",
    "fr": "French", "fre": "French", "fra": "French",
    "es": "Spanish", "spa": "Spanish",
    "de": "German", "ger": "German", "deu": "German",
    "pt": "Portuguese", "por": "Portuguese",
    "nl": "Dutch", "nld": "Dutch", "dut": "Dutch",
    "sv": "Swedish", "swe": "Swedish",
    "no": "Norwegian", "nor": "Norwegian",
    "da": "Danish", "dan": "Danish",
    "fi": "Finnish", "fin": "Finnish",
    "pl": "Polish", "pol": "Polish",
    "ru": "Russian", "rus": "Russian",
    "ja": "Japanese", "jpn": "Japanese",
    "zh": "Chinese", "zho": "Chinese", "chi": "Chinese",
    "ko": "Korean", "kor": "Korean",
    "ar": "Arabic", "ara": "Arabic",
    "hi": "Hindi", "hin": "Hindi",
    "tr": "Turkish", "tur": "Turkish",
    "el": "Greek", "gre": "Greek", "ell": "Greek",
    "he": "Hebrew", "heb": "Hebrew",
    "ro": "Romanian", "ron": "Romanian", "rum": "Romanian",
    "cs": "Czech", "ces": "Czech", "cze": "Czech",
    "hu": "Hungarian", "hun": "Hungarian",
    "uk": "Ukrainian", "ukr": "Ukrainian",
    "th": "Thai", "tha": "Thai",
    "vi": "Vietnamese", "vie": "Vietnamese",
    "id": "Indonesian", "ind": "Indonesian",
    "ms": "Malay", "msa": "Malay", "may": "Malay",
    "la": "Latin", "lat": "Latin",
}


def _resolve_language(lang_code: str | None) -> str:
    """Convert language code (e.g. 'it', 'en-US') to full name. Defaults to English."""
    if not lang_code:
        return "English"
    # Handle codes like "en-US", "pt-BR" — take just the base
    base = lang_code.strip().split("-")[0].split("_")[0].lower()
    return _LANGUAGE_MAP.get(base, lang_code.strip())


# ═══════════════════════════════════════════════════════════════════════════════
# MAPPING TABLES
# ═══════════════════════════════════════════════════════════════════════════════

_FLAG_MAP = {
    "murder": "graphic_violence", "violence": "graphic_violence",
    "gore": "graphic_violence", "blood": "graphic_violence",
    "combat": "graphic_violence", "battle": "graphic_violence",
    "torture": "graphic_violence", "execution": "graphic_violence",
    "mutilation": "graphic_violence", "cannibalism": "graphic_violence",
    "dismemberment": "graphic_violence", "stabbing": "graphic_violence",
    "shooting": "graphic_violence", "beating": "graphic_violence",
    "assault": "graphic_violence", "bloodshed": "graphic_violence",
    "brutality": "graphic_violence", "slaughter": "graphic_violence",
    "killing": "graphic_violence", "homicide": "graphic_violence",
    "decapitation": "graphic_violence", "gun_violence": "graphic_violence",
    "sexual_assault": "sexual_violence", "rape": "sexual_violence",
    "molestation": "sexual_violence", "sexual_abuse": "sexual_violence",
    "non_consensual": "sexual_violence", "coerced_sex": "sexual_violence",
    "sex": "sexual_content", "sex_scenes": "sexual_content",
    "nudity": "sexual_content", "explicit_content": "sexual_content",
    "erotica": "sexual_content", "sexual_themes": "sexual_content",
    "explicit_sex": "sexual_content",
    # NOTE: Removed "intimacy" mapping — emotional intimacy is not
    # the same as sexual content.
    "drugs": "substance_abuse", "drug_use": "substance_abuse",
    "alcoholism": "substance_abuse", "overdose": "substance_abuse",
    "addiction": "substance_abuse", "withdrawal": "substance_abuse",
    # NOTE: Removed mappings that over-flag — alcohol/drinking/smoking
    # mentioned in passing don't equal substance abuse; let the model
    # decide if the actual flag applies.
    "suicide": "self_harm", "suicidal": "self_harm",
    "suicidal_ideation": "self_harm", "self_injury": "self_harm",
    "cutting": "self_harm", "self_mutilation": "self_harm",
    "ptsd": "mental_health", "depression": "mental_health",
    "anxiety": "mental_health", "panic_attacks": "mental_health",
    "eating_disorder": "mental_health", "anorexia": "mental_health",
    "psychosis": "mental_health", "schizophrenia": "mental_health",
    "bipolar": "mental_health", "ocd": "mental_health",
    "insanity": "mental_health", "mental_illness": "mental_health",
    "hallucinations": "mental_health", "paranoia": "mental_health",
    "dissociation": "mental_health", "mania": "mental_health",
    "domestic_violence": "abuse", "domestic_abuse": "abuse",
    "emotional_abuse": "abuse", "physical_abuse": "abuse",
    "psychological_abuse": "abuse", "bullying": "abuse",
    "neglect": "abuse", "gaslighting": "abuse", "cruelty": "abuse",
    "stalking": "abuse", "harassment": "abuse",
    "verbal_abuse": "abuse", "toxic_relationship": "abuse",
    # NOTE: Removed "manipulation" — too broad; manipulation as a plot
    # device (heists, politics) shouldn't auto-flag as abuse.
    "racism": "discrimination", "sexism": "discrimination",
    "homophobia": "discrimination", "transphobia": "discrimination",
    "xenophobia": "discrimination", "antisemitism": "discrimination",
    "ableism": "discrimination", "bigotry": "discrimination",
    "prejudice": "discrimination", "oppression": "discrimination",
    "misogyny": "discrimination",
    "profanity": "strong_language", "cursing": "strong_language",
    "swearing": "strong_language", "slurs": "strong_language",
    "foul_language": "strong_language",
    "dying": "death", "mortality": "death", "character_death": "death",
    "mass_death": "death", "plague": "death", "genocide": "death",
    "poisoning": "death",
    "loss": "grief", "mourning": "grief", "bereavement": "grief",
    "funeral": "grief", "miscarriage": "grief", "orphan": "grief",
    "death_of_child": "child_endangerment", "child_death": "child_endangerment",
    "child_abuse": "child_endangerment", "child_soldier": "child_endangerment",
    "pedophilia": "child_endangerment",
    "captivity": "kidnapping", "imprisonment": "kidnapping",
    "abduction": "kidnapping", "hostage": "kidnapping",
    "confinement": "kidnapping", "human_trafficking": "kidnapping",
    "animal_death": "animal_harm", "animal_cruelty": "animal_harm",
    "pet_death": "animal_harm",
    "body_modification": "body_horror", "mutation": "body_horror",
    "disfigurement": "body_horror", "grotesque": "body_horror",
    "parasites": "body_horror", "decay": "body_horror",
    "abandonment": "trauma", "traumatic_event": "trauma",
    "survivor_guilt": "trauma", "shell_shock": "trauma",
    "enslavement": "slavery", "forced_labor": "slavery",
    "bondage": "slavery", "slave_trade": "slavery",
    "warfare": "war", "military_conflict": "war",
    "armed_conflict": "war", "invasion": "war",
    "bombing": "war", "siege": "war", "terrorism": "war",
    # SKIP — not content warnings
    "dark_magic": "_skip", "transformation": "_skip", "betrayal": "_skip",
    "prophecy": "_skip", "magic": "_skip", "deception": "_skip",
    "secrets": "_skip", "power": "_skip", "corruption": "_skip",
    "rebellion": "_skip", "sacrifice": "_skip", "revenge": "_skip",
    "jealousy": "_skip", "ambition": "_skip", "greed": "_skip",
    "fate": "_skip", "destiny": "_skip", "curse": "_skip",
    "quest": "_skip", "journey": "_skip", "good_vs_evil": "_skip",
    "chosen_one": "_skip", "forbidden_love": "_skip",
    "found_family": "_skip", "redemption": "_skip",
    "duality": "_skip", "legacy": "_skip", "temptation": "_skip",
    "inner_conflict": "_skip", "self_discovery": "_skip",
    "supernatural": "_skip", "monsters": "_skip", "demons": "_skip",
    "ghosts": "_skip", "vampires": "_skip", "werewolves": "_skip",
    "zombies": "_skip", "aliens": "_skip",
}

_ARCH_MAP = {
    "shapeshifter": "trickster", "prophet": "wise_elder",
    "healer": "mentor", "ruler": "royal", "king": "royal",
    "queen": "royal", "prince": "royal", "princess": "royal",
    "emperor": "royal", "lord": "royal", "noble": "royal",
    "god": "gods_or_mythical", "goddess": "gods_or_mythical",
    "deity": "gods_or_mythical", "demigod": "gods_or_mythical",
    "angel": "gods_or_mythical", "immortal": "gods_or_mythical",
    "spirit": "gods_or_mythical", "titan": "gods_or_mythical",
    "monster": "villain", "bully": "villain", "tyrant": "villain",
    "dark_lord": "villain", "nemesis": "villain", "antagonist": "villain",
    "best_friend": "sidekick", "companion": "sidekick",
    "ally": "sidekick", "follower": "sidekick",
    "guide": "mentor", "teacher": "mentor", "trainer": "mentor",
    "sage": "mentor", "counselor": "mentor", "professor": "mentor",
    "student": "child_or_teen", "orphan": "outcast",
    "exile": "outcast", "fugitive": "outcast", "loner": "outcast",
    "hermit": "outcast", "misfit": "outcast", "refugee": "outcast",
    "soldier": "warrior", "knight": "warrior", "guardian": "warrior",
    "protector": "warrior", "fighter": "warrior", "assassin": "warrior",
    "mercenary": "warrior",
    "spy": "trickster", "thief": "trickster", "con_artist": "trickster",
    "rogue": "trickster",
    "investigator": "detective", "sleuth": "detective",
    "inspector": "detective",
    "damsel": "love_interest", "romantic_lead": "love_interest",
    "martyr": "tragic_hero", "fallen_hero": "tragic_hero",
    "underdog": "unlikely_hero", "reluctant_hero": "unlikely_hero",
    "freedom_fighter": "rebel", "revolutionary": "rebel",
}

_GENRE_MAP = {
    # Map old/AI values to new enum values
    "fantasy": "fantasy", "science_fiction": "science_fiction",
    "mystery": "mystery", "thriller": "thriller_and_suspense",
    "suspense": "thriller_and_suspense", "horror": "horror",
    "romance": "romance", "historical_fiction": "historical_fiction",
    "literary_fiction": "literary_fiction", "adventure": "action_and_adventure",
    "action": "action_and_adventure", "dystopian": "science_fiction",
    "memoir": "biographies_and_memoirs", "biography": "biographies_and_memoirs",
    "self_help": "self_help", "philosophy": "philosophy_and_religion",
    "religion": "philosophy_and_religion", "history": "history",
    "science": "science_and_technology", "essay_collection": "essays_and_anthologies",
    "poetry": "poetry", "humor": "humor", "young_adult": "childrens_and_young_adult",
    "middle_grade": "childrens_and_young_adult", "childrens": "childrens_and_young_adult",
    "contemporary_fiction": "contemporary_fiction",
    "womens_fiction": "womens_fiction", "western": "westerns",
    "true_crime": "true_crime", "travel": "travel",
    "sports": "sports_and_outdoors",
    "graphic_novel": "graphic_novels_and_manga",
    "manga": "graphic_novels_and_manga",
    "short_story": "short_story", "classics": "classics",
    "drama": "drama_and_plays", "folklore": "folklore_and_mythology",
    "mythology": "folklore_and_mythology",
}

_TIME_MAP = {
    "stone_age": "prehistoric", "ice_age": "prehistoric", "neolithic": "prehistoric",
    "ancient": "ancient_world", "ancient_greece": "ancient_world",
    "ancient_rome": "ancient_world", "ancient_egypt": "ancient_world",
    "roman_empire": "ancient_world", "classical": "ancient_world",
    "biblical": "ancient_world", "antiquity": "ancient_world",
    "medieval": "medieval", "middle_ages": "medieval", "dark_ages": "medieval",
    "feudal": "medieval", "medieval_fantasy": "medieval",
    "fantasy_medieval": "medieval", "pseudo_medieval": "medieval",
    "viking": "medieval", "arthurian": "medieval",
    "renaissance": "renaissance", "elizabethan": "renaissance",
    "tudor": "renaissance", "16th_century": "renaissance",
    "early_modern": "early_modern", "colonial": "early_modern",
    "17th_century": "early_modern", "enlightenment": "early_modern",
    "18th_century": "18th_century", "georgian": "18th_century",
    "regency": "18th_century",
    "19th_century": "19th_century", "victorian": "19th_century",
    "industrial_revolution": "19th_century", "1800s": "19th_century",
    "steampunk": "19th_century",
    "1920s": "early_20th_century", "1930s": "early_20th_century",
    "1940s": "early_20th_century", "jazz_age": "early_20th_century",
    "wwi": "early_20th_century", "wwii": "early_20th_century",
    "world_war": "early_20th_century",
    "1950s": "mid_20th_century", "1960s": "mid_20th_century",
    "cold_war": "mid_20th_century", "post_war": "mid_20th_century",
    "1970s": "late_20th_century", "1980s": "late_20th_century",
    "1990s": "late_20th_century",
    "contemporary": "contemporary", "modern": "contemporary",
    "present_day": "contemporary", "modern_day": "contemporary",
    "21st_century": "contemporary", "2000s": "contemporary",
    "near_future": "near_future",
    "far_future": "far_future", "distant_future": "far_future",
    "space_age": "far_future", "intergalactic": "far_future",
    "timeless": "timeless_or_unspecified", "unspecified": "timeless_or_unspecified",
    "mythical": "timeless_or_unspecified", "fairy_tale": "timeless_or_unspecified",
    "multiple": "multiple_time_periods", "dual_timeline": "multiple_time_periods",
}

_SETTING_MAP = {
    "city": "urban", "metropolis": "urban", "big_city": "urban",
    "suburbs": "suburban", "neighborhood": "suburban",
    "countryside": "rural", "farmland": "rural", "pastoral": "rural",
    "village": "rural",
    "forest": "wilderness", "jungle": "wilderness", "woods": "wilderness",
    "wild": "wilderness", "frontier": "wilderness", "nature": "wilderness",
    "small_town": "small_town", "hamlet": "small_town",
    "coastal": "coastal_or_island", "island": "coastal_or_island",
    "beach": "coastal_or_island", "seaside": "coastal_or_island",
    "desert": "desert", "wasteland": "desert",
    "arctic": "arctic_or_tundra", "tundra": "arctic_or_tundra",
    "frozen": "arctic_or_tundra", "polar": "arctic_or_tundra",
    "mountain": "mountainous", "alpine": "mountainous",
    "underground": "underground", "subterranean": "underground",
    "cave": "underground", "dungeon": "underground",
    "underwater": "underwater", "deep_sea": "underwater",
    "space": "space", "space_station": "space", "spaceship": "space",
    "alien_world": "other_planet", "alien_planet": "other_planet",
    "exoplanet": "other_planet",
    "fantasy_realm": "fantasy_realm", "fantasy_world": "fantasy_realm",
    "imaginary_world": "fantasy_realm", "fictional_world": "fantasy_realm",
    "magical_world": "fantasy_realm", "enchanted": "fantasy_realm",
    "secondary_world": "fantasy_realm", "imaginary_kingdom": "fantasy_realm",
    "post_apocalyptic": "post_apocalyptic", "dystopian": "post_apocalyptic",
    "ruins": "post_apocalyptic",
    "school": "institutional", "university": "institutional",
    "hospital": "institutional", "prison": "institutional",
    "asylum": "institutional", "academy": "institutional",
    "monastery": "institutional",
    "ocean": "at_sea", "ship": "at_sea", "naval": "at_sea",
    "maritime": "at_sea", "sailing": "at_sea",
    "road_trip": "on_the_road", "journey": "on_the_road",
    "travelling": "on_the_road", "nomadic": "on_the_road",
    "home": "domestic", "household": "domestic", "mansion": "domestic",
    "estate": "domestic", "apartment": "domestic",
    "virtual_reality": "virtual_or_digital", "cyberspace": "virtual_or_digital",
    "simulation": "virtual_or_digital", "metaverse": "virtual_or_digital",
}

_EXP_MAP = {
    "suspenseful": "tense", "thrilling": "tense", "gripping": "tense",
    "nail_biting": "tense",
    "action_filled": "action_packed", "high_octane": "action_packed",
    "engrossing": "immersive", "absorbing": "immersive",
    "captivating": "immersive", "compelling": "immersive",
    "intellectual": "thought_provoking", "cerebral": "thought_provoking",
    "philosophical": "thought_provoking", "reflective": "thought_provoking",
    "heavy": "emotionally_heavy", "emotional": "emotionally_heavy",
    "devastating": "emotionally_heavy", "gut_wrenching": "emotionally_heavy",
    "uplifting": "feel_good", "cheerful": "feel_good", "lighthearted": "feel_good",
    "addictive": "page_turner", "unputdownable": "page_turner",
    "gradual": "slow_burn", "patient": "slow_burn",
    "surprising": "unpredictable", "twisty": "unpredictable",
    "moody": "atmospheric", "evocative": "atmospheric", "gothic": "atmospheric",
    "warm": "heartwarming", "sweet": "heartwarming", "tender": "heartwarming",
    "creepy": "disturbing", "unsettling": "disturbing", "chilling": "disturbing",
    "dark": "disturbing", "macabre": "disturbing",
    "motivating": "inspirational", "empowering": "inspirational",
    "retro": "nostalgic", "sentimental": "nostalgic",
    "informative": "educational", "instructive": "educational",
    "visual": "cinematic", "movie_like": "cinematic", "vivid": "cinematic",
    "comfortable": "cozy", "comforting": "cozy", "gentle": "cozy", "charming": "cozy",
    "poignant": "bittersweet", "wistful": "bittersweet", "melancholic": "bittersweet",
    "enigmatic": "mysterious", "puzzling": "mysterious", "intriguing": "mysterious",
    "grand": "epic_in_scope", "sweeping": "epic_in_scope", "epic": "epic_in_scope",
    "sprawling": "epic_in_scope", "monumental": "epic_in_scope",
    "quirky": "whimsical", "playful": "whimsical", "fanciful": "whimsical",
    "dreamy": "whimsical",
}

_AGE_MAP = {
    # CHILD: 0-12
    "kid": "child", "kids": "child", "children": "child",
    "toddler": "child", "baby": "child", "infant": "child",
    "minor": "child", "preteen": "child", "pre_teen": "child",
    "tween": "child", "elementary": "child", "preschooler": "child",
    # TEEN: 13-17
    "teenager": "teen", "adolescent": "teen", "youth": "teen",
    "high_schooler": "teen", "highschool": "teen",
    "teens": "teen", "teenage": "teen", "13": "teen", "14": "teen",
    "15": "teen", "16": "teen", "17": "teen",
    # YOUNG ADULT: 18-29
    "ya": "young_adult", "twenty_something": "young_adult",
    "twenties": "young_adult", "young": "young_adult",
    "early_adult": "young_adult", "college": "young_adult",
    "college_aged": "young_adult", "university": "young_adult",
    "20s": "young_adult", "early_20s": "young_adult",
    "late_20s": "young_adult", "20_something": "young_adult",
    # ADULT: 30-59
    "middle_aged": "adult", "middle_age": "adult", "mature": "adult",
    "grown_up": "adult", "grownup": "adult", "midlife": "adult",
    "30s": "adult", "40s": "adult", "50s": "adult",
    "early_30s": "adult", "late_30s": "adult",
    "early_40s": "adult", "late_40s": "adult",
    "early_50s": "adult", "late_50s": "adult",
    "thirty_something": "adult", "forty_something": "adult",
    # ELDERLY: 60+
    "elder": "elderly", "old": "elderly", "senior": "elderly",
    "aged": "elderly", "aging": "elderly", "geriatric": "elderly",
    "old_man": "elderly", "old_woman": "elderly", "grandparent": "elderly",
    "grandmother": "elderly", "grandfather": "elderly",
    "60s": "elderly", "70s": "elderly", "80s": "elderly", "90s": "elderly",
    "elderly_person": "elderly",
    # AGELESS: gods, immortals, AIs, supernatural
    "immortal": "ageless", "eternal": "ageless", "timeless": "ageless",
    "ancient": "ageless", "undying": "ageless", "deathless": "ageless",
    "god": "ageless", "goddess": "ageless", "deity": "ageless",
    "spirit": "ageless", "ghost": "ageless", "vampire": "ageless",
    "ai": "ageless", "artificial_intelligence": "ageless",
    "robot": "ageless", "android": "ageless", "construct": "ageless",
    "supernatural": "ageless", "unknown_age": "ageless",
}


# ─────────────────────────────────────────────────────────────────────────────
# NON-FICTION SYNONYM MAPS (Session A infrastructure)
# ─────────────────────────────────────────────────────────────────────────────
# These maps catch common variations the model might produce instead of the
# canonical enum values. The Pass 2 prompt for non-fiction will give the model
# an explicit enum list, so the model usually returns canonical values directly;
# these maps are insurance against off-script variations.
#
# Substring-match safety: keys are deliberately specific enough that
# _sanitize_single's substring fallback (when v ⊆ mk or mk ⊆ v with len≥4) won't
# produce false positives. Avoid 3-letter keys that could be embedded in unrelated
# strings. We also avoid synonyms that overlap across different enums.

# ── High-variation enums (more thorough coverage) ──────────────────────────────

_SUBTYPE_MAP = {
    # self_help
    "self_help_book": "self_help", "instructional": "self_help",
    "how_to": "self_help", "howto": "self_help", "guide": "self_help",
    "advice": "self_help", "productivity": "self_help",
    # memoir_biography
    "memoir": "memoir_biography", "biography": "memoir_biography",
    "autobiography": "memoir_biography", "bio": "memoir_biography",
    "life_story": "memoir_biography",
    # history_narrative
    "history": "history_narrative", "narrative_nonfiction": "history_narrative",
    "narrative_non_fiction": "history_narrative",
    "historical_nonfiction": "history_narrative",
    "journalism": "history_narrative", "longform": "history_narrative",
    # academic_textbook
    "textbook": "academic_textbook", "academic": "academic_textbook",
    "scholarly": "academic_textbook", "reference_book": "academic_textbook",
    "monograph": "academic_textbook",
    # popular_science
    "popsci": "popular_science", "pop_science": "popular_science",
    "science": "popular_science", "science_writing": "popular_science",
    "nature_writing": "popular_science",
    # philosophy_religion
    "philosophy": "philosophy_religion", "religion": "philosophy_religion",
    "spirituality": "philosophy_religion", "theology": "philosophy_religion",
    "religious": "philosophy_religion",
    # business_economics
    "business": "business_economics", "economics": "business_economics",
    "finance": "business_economics", "leadership": "business_economics",
    "management": "business_economics", "entrepreneurship": "business_economics",
    # health_fitness
    "health": "health_fitness", "fitness": "health_fitness",
    "wellness": "health_fitness", "nutrition": "health_fitness",
    "diet_book": "health_fitness", "medical": "health_fitness",
    # cooking_food
    "cookbook": "cooking_food", "cooking": "cooking_food",
    "recipes": "cooking_food", "food_writing": "cooking_food",
    "culinary": "cooking_food",
    # travel_nature
    "travel": "travel_nature", "travelogue": "travel_nature",
    "nature": "travel_nature", "outdoor": "travel_nature",
    "wilderness": "travel_nature",
    # true_crime
    "crime": "true_crime", "true_crime_book": "true_crime",
    "criminal_investigation": "true_crime",
}

_AUDIENCE_MAP = {
    # beginner
    "novice": "beginner", "newbie": "beginner", "entry_level": "beginner",
    "starter": "beginner", "introductory": "beginner",
    # intermediate
    "mid_level": "intermediate", "moderate": "intermediate",
    "some_experience": "intermediate",
    # advanced
    "expert": "advanced", "experienced": "advanced",
    "professional_level": "advanced", "high_level": "advanced",
    # general_reader
    "general": "general_reader", "lay_reader": "general_reader",
    "general_audience": "general_reader", "lay_person": "general_reader",
    "popular_audience": "general_reader", "everyone": "general_reader",
    # specialist
    "specialist_audience": "specialist", "expert_only": "specialist",
    "academic_audience": "specialist", "domain_expert": "specialist",
}

_STRUCTURE_MAP = {
    # linear_argument
    "linear": "linear_argument", "sequential": "linear_argument",
    "cumulative": "linear_argument", "building_argument": "linear_argument",
    # episodic_chapters
    "episodic": "episodic_chapters", "standalone_chapters": "episodic_chapters",
    "self_contained_chapters": "episodic_chapters",
    "essay_collection": "episodic_chapters",
    # case_studies
    "case_study": "case_studies", "examples_based": "case_studies",
    "vignettes": "case_studies",
    # reference
    "reference_work": "reference", "encyclopedic": "reference",
    "lookup": "reference", "dictionary_style": "reference",
    # workbook
    "exercise_book": "workbook", "practice_book": "workbook",
    "workbook_style": "workbook",
    # mixed
    "hybrid": "mixed", "varied": "mixed", "multiple_formats": "mixed",
}

_TONE_REG_MAP = {
    # academic
    "scholarly": "academic", "formal": "academic", "rigorous": "academic",
    "scientific": "academic",
    # conversational
    "casual": "conversational", "informal": "conversational",
    "approachable": "conversational", "friendly": "conversational",
    # inspirational
    "uplifting_tone": "inspirational", "motivational": "inspirational",
    "encouraging": "inspirational",
    # sobering
    "serious": "sobering", "grave": "sobering", "weighty": "sobering",
    "solemn": "sobering",
    # witty
    "humorous": "witty", "amusing": "witty", "clever": "witty",
    "tongue_in_cheek": "witty",
    # dense
    "complex_prose": "dense", "challenging_prose": "dense", "thick": "dense",
    # breezy
    "light": "breezy", "easy_reading": "breezy", "quick_read_tone": "breezy",
    "fluffy": "breezy",
}

_CONCLUSION_MAP = {
    # optimistic
    "positive": "optimistic", "upbeat": "optimistic",
    "encouraging_ending": "optimistic",
    # cautionary
    "warning": "cautionary", "warning_tale": "cautionary",
    "cautionary_tale": "cautionary",
    # open_ended
    "unresolved": "open_ended", "ambiguous": "open_ended",
    "inconclusive": "open_ended", "open": "open_ended",
    # definitive
    "conclusive": "definitive", "settled": "definitive",
    "resolved": "definitive", "final": "definitive",
    # provocative
    "challenging": "provocative", "controversial": "provocative",
    "thought_provoking_ending": "provocative",
    # hopeful
    "encouraging": "hopeful", "promising": "hopeful",
}

_NARRATIVE_SHAPE_MAP = {
    # triumph
    "success_story": "triumph", "overcoming": "triumph",
    "redemption": "triumph", "triumphant": "triumph",
    "rise_to_success": "triumph",
    # cautionary
    "tragedy": "cautionary", "tragic": "cautionary",
    "downfall": "cautionary", "warning_story": "cautionary",
    # witness
    "observer": "witness", "bystander": "witness", "documentary": "witness",
    "first_person_account": "witness",
    # survival
    "survival_story": "survival", "endurance": "survival",
    "trauma_recovery": "survival",
    # coming_of_age
    "bildungsroman": "coming_of_age", "growing_up": "coming_of_age",
    "youth_to_adulthood": "coming_of_age",
}

# ── Low-variation enums (minimal coverage — model usually returns canonical) ───

_COMMITMENT_MAP = {
    "quick": "quick_read", "easy": "quick_read", "brief": "quick_read",
    "moderate_effort": "casual_application", "some_practice": "casual_application",
    "intensive": "serious_practice", "deep_practice": "serious_practice",
    "rigorous_practice": "serious_practice",
}

_SUBJECT_REL_MAP = {
    "self_written": "autobiographical", "first_person": "autobiographical",
    "authorized_biography": "authorized",
    "unauthorized_biography": "unauthorized",
    "academic_biography": "scholarly", "scholarly_biography": "scholarly",
}

_HIST_PERSP_MAP = {
    "leaders": "great_figures", "notable_figures": "great_figures",
    "famous_people": "great_figures", "kings_and_leaders": "great_figures",
    "ordinary_people": "everyday_people", "common_people": "everyday_people",
    "common_folk": "everyday_people",
    "community": "specific_community", "group_focused": "specific_community",
    "worldwide": "global", "international": "global", "world_history": "global",
}

_ACAD_LEVEL_MAP = {
    "college": "undergraduate", "bachelor": "undergraduate",
    "undergrad": "undergraduate",
    "masters": "graduate", "doctoral": "graduate", "phd": "graduate",
    "post_graduate": "graduate", "postgraduate": "graduate",
    "practitioner": "professional", "working_professional": "professional",
    "introduction": "intro_survey", "intro": "intro_survey",
    "survey_course": "intro_survey",
}

_PHIL_FOCUS_MAP = {
    "abstract": "theoretical", "conceptual": "theoretical",
    "applied": "practical", "everyday": "practical", "lived": "practical",
    "religious_practice": "devotional", "prayer_focused": "devotional",
    "meditative": "devotional",
    "history_of_philosophy": "historical", "history_of_religion": "historical",
}

_BIZ_ROLE_MAP = {
    "entrepreneur": "founder", "ceo": "founder", "startup_founder": "founder",
    "executive": "manager", "team_lead": "manager", "supervisor": "manager",
    "employee": "individual_contributor", "worker": "individual_contributor",
    "ic": "individual_contributor",
    "anyone": "general", "general_audience": "general",
}

_HEALTH_BASIS_MAP = {
    "research_based": "clinical_research", "evidence_based": "clinical_research",
    "scientific_research": "clinical_research", "peer_reviewed": "clinical_research",
    "expert_experience": "practitioner_experience",
    "doctor_experience": "practitioner_experience",
    "clinical_practice": "practitioner_experience",
    "personal_stories": "anecdotal", "testimonials": "anecdotal",
    "case_reports": "anecdotal",
    "combined": "mixed", "multiple_sources": "mixed",
}

_RECIPE_DIFF_MAP = {
    "easy": "beginner", "simple": "beginner", "basic": "beginner",
    "medium": "intermediate", "moderate": "intermediate",
    "expert": "advanced", "professional": "advanced", "complex": "advanced",
    "varied_difficulty": "mixed", "all_levels": "mixed",
}

_COOKBOOK_PURPOSE_MAP = {
    "recipes_only": "recipe_collection", "recipe_book": "recipe_collection",
    "techniques": "technique", "skills": "technique", "methods": "technique",
    "food_memoir": "food_narrative", "food_essays": "food_narrative",
    "food_stories": "food_narrative",
    "diet_plan": "dietary_program", "meal_plan": "dietary_program",
    "diet": "dietary_program", "eating_plan": "dietary_program",
}

_TRAVEL_STYLE_MAP = {
    "backpacking": "adventure", "extreme_travel": "adventure",
    "expedition": "adventure",
    "cultural_immersion": "cultural", "heritage": "cultural",
    "historical_travel": "cultural",
    "nature_focused": "nature", "outdoor_travel": "nature",
    "wildlife": "nature",
    "high_end": "luxury", "upscale": "luxury", "premium_travel": "luxury",
    "cheap_travel": "budget", "shoestring": "budget", "low_cost": "budget",
    "varied_styles": "mixed", "multiple_styles": "mixed",
}

_TC_CASE_MAP = {
    "one_case": "single_case", "single_crime": "single_case",
    "single_event": "single_case",
    "multiple_crimes": "multiple_cases", "several_cases": "multiple_cases",
    "anthology": "multiple_cases",
    "analysis_of_patterns": "pattern_analysis", "systematic": "pattern_analysis",
    "serial_analysis": "pattern_analysis",
}

_TC_RESOLUTION_MAP = {
    "case_solved": "solved", "perpetrator_caught": "solved",
    "case_unsolved": "unsolved", "mystery": "unsolved",
    "cold": "cold_case", "old_case": "cold_case",
    "in_progress": "ongoing", "active_case": "ongoing",
    "active_investigation": "ongoing",
}

_TC_PERSPECTIVE_MAP = {
    "reporter": "journalist", "investigative_journalist": "journalist",
    "police": "law_enforcement", "detective": "law_enforcement",
    "fbi": "law_enforcement", "cop": "law_enforcement",
    "victim_family": "family", "loved_ones": "family", "relatives": "family",
    "scholar": "academic", "researcher": "academic", "professor": "academic",
    "criminal": "perpetrator", "killer_perspective": "perpetrator",
    "from_inside": "perpetrator",
}


# ═══════════════════════════════════════════════════════════════════════════════
# SANITIZATION
# ═══════════════════════════════════════════════════════════════════════════════

def _sanitize_enum_list(values, valid_set, fmap=None, fallback="none"):
    if not isinstance(values, list): return [fallback]
    result = []
    for v in values:
        if not isinstance(v, str): continue
        vl = v.lower().strip().replace(" ", "_").replace("-", "_")
        if vl in valid_set: result.append(vl)
        elif fmap:
            if vl in fmap:
                m = fmap[vl]
                if m != "_skip": result.append(m)
            else:
                vn = vl.replace("_", "")
                for mk, mv in fmap.items():
                    if mk.replace("_", "") == vn and mv != "_skip":
                        result.append(mv); break
    seen = set()
    return [x for x in result if not (x in seen or seen.add(x))] or [fallback]

def _sanitize_single(value, valid_set, fmap=None, fallback="other"):
    if not isinstance(value, str): return fallback
    v = value.lower().strip().replace(" ", "_").replace("-", "_")
    if v in valid_set: return v
    if fmap:
        if v in fmap: return fmap[v]
        vn = v.replace("_", "")
        for mk, mv in fmap.items():
            if mk.replace("_", "") == vn: return mv
        for mk, mv in fmap.items():
            if len(v) >= 4 and (v in mk or mk in v): return mv
    return fallback


def _sanitize_age(value):
    """
    Normalize an age_category value to a canonical AgeCategory string or None.

    Matching is intentionally STRICT — we'd rather return None ("unknown")
    than guess wrong. The model is given an explicit enum list in the prompt,
    so it almost always returns canonical-looking values. The synonym map
    catches the common variations. Anything else returns None.

    Returns None for:
      - non-string input
      - empty / whitespace-only strings
      - literal "null", "none", "unknown", "n_a", "na", "?"
      - any value that can't be matched canonically or via _AGE_MAP

    Otherwise returns the canonical lower-case enum value.

    Matching tiers (in order):
      1. Canonical exact match (after lowercase + whitespace/dash normalization)
      2. Synonym map exact match
      3. Collapsed-underscore equivalence ("young adult" → "young_adult" by
         removing all underscores and comparing — safely catches inconsistent
         formatting without the false-positive risk of substring matching)

    Note: A previous version had a substring fallback that produced wrong
    results (e.g., "teen-aged" → "elderly" because "aged" was inside the
    elderly synonym map). That tier has been removed in favor of returning
    None when the value isn't clearly classifiable.
    """
    if not isinstance(value, str):
        return None
    v = value.lower().strip().replace(" ", "_").replace("-", "_")
    if not v or v in ("null", "none", "unknown", "n_a", "na", "?", "n/a"):
        return None
    # Tier 1: canonical exact match
    if v in _VALID_AGE:
        return v
    # Tier 2: synonym map exact match
    if v in _AGE_MAP:
        return _AGE_MAP[v]
    # Tier 3: collapsed-underscore equivalence (safe — no substring matching)
    vn = v.replace("_", "")
    for mk, mv in _AGE_MAP.items():
        if mk.replace("_", "") == vn:
            return mv
    # No safe match — return None rather than guess
    return None

def _normalize_theme(raw):
    r = raw.lower().strip()
    if r in MASTER_THEMES_SET: return r
    for m in MASTER_THEMES:
        ml = m.lower()
        if r in ml or ml in r: return ml
        rw = set(r.replace("-", " ").split())
        mw = set(ml.replace("-", " ").split())
        if mw and len(rw & mw) / len(mw) >= 0.6: return ml
    return None

def _normalize_sub_genre(raw):
    r = raw.lower().strip()
    if r in MASTER_SUB_GENRES_SET: return r
    for m in MASTER_SUB_GENRES:
        ml = m.lower()
        if r in ml or ml in r: return ml
        rw = set(r.replace("-", " ").split())
        mw = set(ml.replace("-", " ").split())
        if mw and len(rw & mw) / len(mw) >= 0.5: return ml
    return r  # Allow through for now

def _normalize_category(raw):
    r = raw.lower().strip()
    if r in MASTER_CATEGORIES_SET: return r
    for m in MASTER_CATEGORIES:
        ml = m.lower()
        if r in ml or ml in r: return ml
        rw = set(r.replace("-", " ").split())
        mw = set(ml.replace("-", " ").split())
        if mw and len(rw & mw) / len(mw) >= 0.5: return ml
    return None

def _is_valid_character(char):
    if not isinstance(char, dict): return False
    name = char.get("name", "").strip()
    if not name or len(name) < 2: return False
    generic = {
        "the stranger", "stranger", "the girl", "the boy", "the man",
        "the woman", "the old man", "the old woman", "unknown", "unnamed",
        "the figure", "the shadow", "the voice", "the creature", "the beast",
        "the enemy", "the guard", "the soldier", "the servant", "narrator",
        "the protagonist", "the hero", "the heroine", "the child",
        "the king", "the queen", "the prince", "the princess",
        "the wizard", "the witch", "the knight", "the dragon",
    }
    if name.lower() in generic: return False
    summary = char.get("arc_summary", "").strip()
    if not summary or len(summary) < 10: return False
    return True

def _sanitize_chunk_data(data):
    if "themes_detected" in data and isinstance(data["themes_detected"], list):
        norm = []
        for raw in data["themes_detected"]:
            n = raw if isinstance(raw, str) else raw.get("name", "") if isinstance(raw, dict) else ""
            if n:
                m = _normalize_theme(n)
                if m: norm.append(m)
        seen = set()
        data["themes_detected"] = [t for t in norm if not (t in seen or seen.add(t))] or ["identity and self-discovery"]

    if "theme_prominences" in data and isinstance(data["theme_prominences"], dict):
        np = {}
        for rn, sc in data["theme_prominences"].items():
            m = _normalize_theme(rn)
            if m and isinstance(sc, (int, float)): np[m] = max(1, min(10, int(sc)))
        data["theme_prominences"] = np
    else:
        data["theme_prominences"] = {t: 5 for t in data.get("themes_detected", [])}

    if "characters_present" in data and isinstance(data["characters_present"], list):
        data["characters_present"] = [c for c in data["characters_present"] if _is_valid_character(c)]

    for char in data.get("characters_present", []):
        if isinstance(char, dict):
            if "archetypes" in char:
                char["archetypes"] = _sanitize_enum_list(char["archetypes"], _VALID_ARCH, _ARCH_MAP, "other")[:3]
            if "gender" not in char or char.get("gender", "").lower() not in ("male", "female", "non-binary", "unknown"):
                char["gender"] = "unknown"
            else:
                char["gender"] = char["gender"].lower().strip()
            # Normalize age_category to a canonical value or None
            char["age_category"] = _sanitize_age(char.get("age_category"))

    if "content_flags" in data:
        data["content_flags"] = _sanitize_enum_list(data["content_flags"], _VALID_FLAGS, _FLAG_MAP, "none")
    if "humor_types" in data:
        data["humor_types"] = _sanitize_enum_list(data["humor_types"], _VALID_HUMOR, None, "none")

    # Score fields: clamp valid numbers to 1–10; default to 5 when the model
    # returned null, omitted the field, or returned a non-numeric value.
    for f in ["humor_density", "tone", "readability_score", "violence_level",
              "pace_score", "romance_level", "worldbuilding_level"]:
        val = data.get(f)
        if isinstance(val, bool):
            data[f] = 5
        elif isinstance(val, (int, float)):
            data[f] = max(1, min(10, int(val)))
        else:
            data[f] = 5

    if "tone_light_to_dark" in data and "tone" not in data:
        data["tone"] = data["tone_light_to_dark"]
    if "readability_score" not in data:
        data["readability_score"] = 5
    return data

def _sanitize_holistic_data(data):
    if "content_flags" in data:
        data["content_flags"] = _sanitize_enum_list(data["content_flags"], _VALID_FLAGS, _FLAG_MAP, "none")
    if "reading_experience" in data:
        data["reading_experience"] = _sanitize_enum_list(data["reading_experience"], _VALID_EXP, _EXP_MAP, "immersive")[:4]
    if "pov_types" in data:
        data["pov_types"] = _sanitize_enum_list(data["pov_types"], _VALID_POV, None, "third_person_limited")[:3]
    if "humor_types" in data:
        data["humor_types"] = _sanitize_enum_list(data["humor_types"], _VALID_HUMOR, None, "none")[:3]
    else:
        data["humor_types"] = ["none"]

    # Categories
    if "categories" in data and isinstance(data["categories"], list):
        norm = [_normalize_category(c) for c in data["categories"] if isinstance(c, str)]
        data["categories"] = [c for c in norm if c][:3]
    if not data.get("categories"):
        data["categories"] = ["standalone"]

    # Sub-genres (deduplicate after normalizing)
    if "sub_genres" in data and isinstance(data["sub_genres"], list):
        norm = [_normalize_sub_genre(sg) for sg in data["sub_genres"] if isinstance(sg, str)]
        seen_sg = set()
        deduped = []
        for s in norm:
            if s and s.lower() not in seen_sg:
                seen_sg.add(s.lower())
                deduped.append(s)
        data["sub_genres"] = deduped[:4] or ["literary fiction"]
    else:
        data["sub_genres"] = ["literary fiction"]

    # Genre
    if "genre" in data:
        g = data["genre"]
        if isinstance(g, list):
            for item in g:
                if isinstance(item, str):
                    mapped = _sanitize_single(item, _VALID_GENRES, _GENRE_MAP, None)
                    if mapped and mapped != "other":
                        data["genre"] = mapped; break
            else:
                data["genre"] = "other"
        elif isinstance(g, str):
            data["genre"] = _sanitize_single(g, _VALID_GENRES, _GENRE_MAP, "other")

    # Book type
    if "book_type" in data:
        bt = data["book_type"]
        if isinstance(bt, list):
            for item in bt:
                if isinstance(item, str) and item.lower().strip() in _VALID_BTYPES:
                    data["book_type"] = item.lower().strip(); break
            else:
                data["book_type"] = "fiction"
        elif isinstance(bt, str) and bt.lower().strip() not in _VALID_BTYPES:
            data["book_type"] = "fiction"

    # Setting
    if "setting" in data and isinstance(data["setting"], dict):
        s = data["setting"]
        s["time_period"] = _sanitize_single(
            s.get("time_period", ""), _VALID_TIME, _TIME_MAP, "timeless_or_unspecified")
        s["setting_type"] = _sanitize_single(
            s.get("setting_type", ""), _VALID_SETTING, _SETTING_MAP, "fantasy_realm")

    if "character_arcs" not in data or not isinstance(data.get("character_arcs"), dict):
        data["character_arcs"] = {}
    if "character_genders" not in data or not isinstance(data.get("character_genders"), dict):
        data["character_genders"] = {}
    if "character_archetypes" not in data or not isinstance(data.get("character_archetypes"), dict):
        data["character_archetypes"] = {}
    else:
        # Sanitize each character's archetype list
        sanitized_archetypes = {}
        for char_name, archs in data["character_archetypes"].items():
            if isinstance(archs, list):
                sanitized_archetypes[char_name] = _sanitize_enum_list(
                    archs, _VALID_ARCH, _ARCH_MAP, "other")[:3]
            elif isinstance(archs, str):
                sanitized_archetypes[char_name] = _sanitize_enum_list(
                    [archs], _VALID_ARCH, _ARCH_MAP, "other")[:3]
            else:
                sanitized_archetypes[char_name] = ["other"]
        data["character_archetypes"] = sanitized_archetypes
    if "character_ages" not in data or not isinstance(data.get("character_ages"), dict):
        data["character_ages"] = {}
    else:
        # Sanitize each character's age value to a canonical AgeCategory or None
        sanitized_ages = {}
        for char_name, age_val in data["character_ages"].items():
            if isinstance(char_name, str) and char_name.strip():
                sanitized_ages[char_name.strip()] = _sanitize_age(age_val)
        data["character_ages"] = sanitized_ages
    if "ranked_themes" in data and isinstance(data["ranked_themes"], list):
        norm = []
        for t in data["ranked_themes"]:
            if isinstance(t, str):
                m = _normalize_theme(t)
                if m: norm.append(m)
        data["ranked_themes"] = norm[:10]
    if not data.get("ranked_themes"):
        data["ranked_themes"] = []
    if "ranked_characters" in data and isinstance(data["ranked_characters"], list):
        data["ranked_characters"] = [c.strip() for c in data["ranked_characters"]
                                      if isinstance(c, str) and c.strip()][:8]
    if not data.get("ranked_characters"):
        data["ranked_characters"] = []
    if "age_target" in data and isinstance(data["age_target"], (int, float)):
        data["age_target"] = max(1, min(10, int(data["age_target"])))
    return data

def _safe_parse_json(raw):
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = "\n".join(ln for ln in cleaned.split("\n") if not ln.strip().startswith("```"))
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        s, end = cleaned.find("{"), cleaned.rfind("}") + 1
        if s >= 0 and end > s:
            try: return json.loads(cleaned[s:end])
            except json.JSONDecodeError: pass
        logger.error(f"JSON parse failed: {e}\nRaw: {raw[:500]}")
        raise ValueError(f"Could not parse JSON: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# AGGREGATION
# ═══════════════════════════════════════════════════════════════════════════════

def aggregate_analysis(chunk_analyses, holistic, extraction):
    def wavg(fn):
        tw = sum(c.word_count for c in chunk_analyses) or 1
        return max(1, min(10, round(sum(fn(c) * c.word_count for c in chunk_analyses) / tw)))

    metadata = BookMetadata(
        title=holistic.title, author=holistic.author,
        book_type=holistic.book_type, genre=holistic.genre,
        sub_genres=holistic.sub_genres,
        publisher=extraction.pdf_metadata.publisher,
        publish_year=extraction.pdf_metadata.publish_year,
        language=_resolve_language(extraction.pdf_metadata.language),
        isbn=extraction.pdf_metadata.isbn)

    ratings = ContentRatings(
        tone=wavg(lambda c: c.tone),
        readability=wavg(lambda c: c.readability_score),
        violence=wavg(lambda c: c.violence_level),
        age_target=holistic.age_target,
        pace=wavg(lambda c: c.pace_score),
        worldbuilding=wavg(lambda c: c.worldbuilding_level),
        humor=wavg(lambda c: c.humor_density),
        romance=wavg(lambda c: c.romance_level))

    sentences = [s.strip() for s in extraction.full_text.split(".") if s.strip()]
    asl = sum(len(s.split()) for s in sentences) / len(sentences) if sentences else 15.0
    wpp = extraction.total_words / max(extraction.total_pages, 1)
    wpm = max(100, min(400, 250 * (1.0 - (ratings.tone - 5) * 0.08)))

    computed = ComputedStats(
        total_words=extraction.total_words, total_pages=extraction.total_pages,
        avg_sentence_length=round(asl, 1), avg_words_per_page=round(wpp, 1),
        estimated_read_time_hours=round(extraction.total_words / wpm / 60, 2))

    themes = _aggregate_themes(chunk_analyses, holistic)
    characters = _aggregate_characters(chunk_analyses, holistic)

    hcounts = {}
    for c in chunk_analyses:
        for ht in c.humor_types: hcounts[ht] = hcounts.get(ht, 0) + 1
    top_h = sorted(hcounts, key=hcounts.get, reverse=True)[:3]
    humor = HumorProfile(humor_density=ratings.humor,
                          primary_humor_types=top_h or [HumorType.NONE])

    # Content flags: frequency-based, dynamically capped at 0-6
    chunk_flag_lists = [list(c.content_flags) for c in chunk_analyses]
    cflags = _aggregate_content_flags(
        chunk_flag_lists, list(holistic.content_flags), len(chunk_analyses))

    return BookAnalysis(
        metadata=metadata, themes=themes,
        categories=holistic.categories,
        characters=characters,
        setting=holistic.setting, ratings=ratings, computed_stats=computed,
        humor=humor, pov=holistic.pov_types, pov_notes=holistic.pov_notes,
        reading_experience=holistic.reading_experience,
        sad_ending=holistic.sad_ending, cliffhanger=holistic.cliffhanger,
        ending_notes=holistic.ending_notes, content_flags=cflags,
        overall_summary=holistic.overall_summary)


def _aggregate_content_flags(chunk_flag_lists, holistic_flags, num_chunks):
    """
    Frequency-based content flag aggregation with dynamic cap.

    A flag only makes the final list if it appears in enough chunks to be
    considered a recurring/significant element of the book — not a one-off
    mention. The threshold scales with book length:
      - Very short (1-5 chunks):    flag must appear in 2+ chunks
      - Short-medium (6-15 chunks): flag must appear in 3+ chunks
      - Medium-long (16-30 chunks): flag must appear in 4+ chunks
      - Long (31+ chunks):          flag must appear in ~15% of chunks

    Pass 2's flags act as a CONFIRMATION signal — a flag that Pass 2 also
    identified gets a boost (treated as if it appeared in 1 extra chunk),
    since Pass 2 has full-book context that Pass 1 chunks lack.

    Returns up to 6 flags ranked by frequency. Returns [NONE] if nothing
    meets the threshold.

    Args:
        chunk_flag_lists: list of lists of ContentFlag (one list per chunk)
        holistic_flags: list of ContentFlag from Pass 2 holistic analysis
        num_chunks: total number of chunks analyzed (for threshold scaling)
    """
    # Determine threshold based on book length
    if num_chunks <= 5:
        threshold = 2
    elif num_chunks <= 15:
        threshold = 3
    elif num_chunks <= 30:
        threshold = 4
    else:
        threshold = max(4, round(num_chunks * 0.15))

    # Count chunk-level appearances per flag
    flag_counts: dict = {}
    for chunk_flags in chunk_flag_lists:
        # Use set per chunk so a single chunk listing the same flag multiple
        # times doesn't double-count (defensive)
        for flag in set(chunk_flags):
            if flag == ContentFlag.NONE:
                continue
            flag_counts[flag] = flag_counts.get(flag, 0) + 1

    # Pass 2 confirmation boost: if Pass 2 flagged it, give it +1
    # (treats Pass 2's whole-book view as roughly equivalent to 1 chunk vote)
    p2_flag_set = set(holistic_flags) - {ContentFlag.NONE}
    for flag in p2_flag_set:
        flag_counts[flag] = flag_counts.get(flag, 0) + 1

    # Filter by threshold and rank by frequency
    qualifying = [(flag, count) for flag, count in flag_counts.items()
                   if count >= threshold]
    qualifying.sort(key=lambda x: (-x[1], x[0].value))  # frequency DESC, then alphabetical

    # Dynamic cap: 0-6 flags depending on what qualified
    final_flags = [flag for flag, _ in qualifying[:6]]

    return final_flags or [ContentFlag.NONE]


def _aggregate_themes(chunks, holistic):
    """
    Use Pass 2's ranked_themes as SOURCE OF TRUTH for which themes
    make the final cut and their order. Pass 1 provides prominence scores.
    Cap at 10 themes max.
    """
    td = {}
    for c in chunks:
        for tn in c.themes_detected:
            k = tn.lower().strip()
            if k not in td: td[k] = {"max": 0, "cnt": 0}
            td[k]["max"] = max(td[k]["max"], c.theme_prominences.get(k, 5))
            td[k]["cnt"] += 1

    ranked = holistic.ranked_themes if holistic.ranked_themes else []

    result = []
    for i, theme_name in enumerate(ranked[:10]):
        tn = theme_name.lower().strip()
        if tn in td:
            prom = td[tn]["max"]
        else:
            prom = max(4, 10 - i)
        result.append(Theme(name=tn, prominence=max(1, min(10, prom))))

    if not result:
        tc = len(chunks)
        min_c = 1 if tc <= 5 else 2
        result = sorted(
            [Theme(name=k, prominence=max(1, min(10, d["max"])))
             for k, d in td.items() if d["cnt"] >= min_c],
            key=lambda t: t.prominence, reverse=True)[:10]

    return result


def _aggregate_characters(chunks, holistic):
    """
    Use Pass 2's ranked_characters as SOURCE OF TRUTH for which characters
    make the final cut and their order. Pass 2's character_genders provides
    gender. Cap at 8 characters max. No fake summaries — drop if no arc.
    """
    raw = {}
    for c in chunks:
        for ch in c.characters_present:
            k = ch.name.lower().strip()
            if k not in raw:
                raw[k] = {"name": ch.name,
                          "imp": [], "arch": [], "age": None,
                          "gender": "unknown", "cnt": 0}
            d = raw[k]
            d["imp"].append(ch.importance); d["arch"].extend(ch.archetypes)
            d["cnt"] += 1
            if ch.age_category: d["age"] = ch.age_category
            if hasattr(ch, "gender") and ch.gender and ch.gender != "unknown":
                d["gender"] = ch.gender

    merged = _merge_chars(raw)
    arcs = holistic.character_arcs
    genders = holistic.character_genders if holistic.character_genders else {}
    ranked = holistic.ranked_characters if holistic.ranked_characters else []

    # Build lookup from ranked names to merged character data
    def _find_merged_key(name):
        nl = name.lower().strip()
        if nl in merged: return nl
        for mk in merged:
            if _same_char(nl, mk): return mk
        return None

    # If Pass 2 provided rankings, use them as the definitive order
    if ranked:
        result = []
        used_keys = set()
        for char_name in ranked[:8]:
            mk = _find_merged_key(char_name)
            if mk is None or mk in used_keys:
                continue
            used_keys.add(mk)
            cd = merged[mk]
            imp = max(cd["imp"])

            ac = {}
            for a in cd["arch"]: ac[a] = ac.get(a, 0) + 1
            top_a = sorted(ac, key=ac.get, reverse=True)[:3] or [CharacterArchetype.OTHER]

            arc = _find_arc(cd, arcs)
            if not arc or len(arc.strip()) < 10:
                continue

            # Gender: prefer Pass 2's answer, then chunk data, then unknown
            gender = "unknown"
            for gn, gv in genders.items():
                if _same_char(gn.lower(), mk):
                    gender = gv.lower().strip()
                    break
            if gender not in ("male", "female", "non-binary"):
                gender = cd.get("gender", "unknown")
            if gender not in ("male", "female", "non-binary", "unknown"):
                gender = "unknown"

            result.append(Character(
                name=cd["name"],
                importance=imp,  # Chunk-based MAX: stable across runs
                gender=gender, archetypes=top_a,
                arc_summary=arc, age_category=cd["age"]))
        return result

    # Fallback: no rankings from Pass 2, use chunk data
    result = []
    for k, cd in merged.items():
        imp = max(cd["imp"])
        if cd["cnt"] == 1 and imp <= 3: continue

        ac = {}
        for a in cd["arch"]: ac[a] = ac.get(a, 0) + 1
        top_a = sorted(ac, key=ac.get, reverse=True)[:3] or [CharacterArchetype.OTHER]

        arc = _find_arc(cd, arcs)
        if not arc or len(arc.strip()) < 10:
            continue

        gender = cd.get("gender", "unknown")
        for gn, gv in genders.items():
            if _same_char(gn.lower(), k):
                gender = gv.lower().strip(); break
        if gender not in ("male", "female", "non-binary", "unknown"):
            gender = "unknown"

        result.append(Character(
            name=cd["name"], importance=imp,
            gender=gender, archetypes=top_a,
            arc_summary=arc, age_category=cd["age"]))

    return sorted(result, key=lambda c: c.importance, reverse=True)[:8]


def _merge_chars(raw):
    keys = list(raw.keys())
    mi = {}
    for i in range(len(keys)):
        if keys[i] in mi: continue
        for j in range(i+1, len(keys)):
            if keys[j] in mi: continue
            if _same_char(keys[i], keys[j]):
                p = keys[i] if len(keys[i]) >= len(keys[j]) else keys[j]
                s = keys[j] if p == keys[i] else keys[i]
                while p in mi: p = mi[p]
                mi[s] = p
    result = {}
    for k, cd in raw.items():
        p = k
        while p in mi: p = mi[p]
        if p not in result:
            pd = raw.get(p, cd)
            result[p] = {"name": pd["name"], "all": {p},
                         "imp": list(pd["imp"]), "arch": list(pd["arch"]),
                         "age": pd["age"], "cnt": pd["cnt"]}
        if k != p:
            r = result[p]; r["all"].add(k)
            r["imp"].extend(cd["imp"]); r["arch"].extend(cd["arch"])
            r["cnt"] += cd["cnt"]
            if cd["age"] and not r["age"]: r["age"] = cd["age"]
            if len(cd["name"]) > len(r["name"]): r["name"] = cd["name"]
    return result

def _same_char(a, b):
    """
    Match two character names safely. Returns True only if names refer to
    the same character.

    Rules (in order):
    1. Exact match (case-insensitive) → True
    2. Both names have multiple words → require same first word
       This rejects siblings/spouses with shared surnames:
         "Ron Weasley" ↔ "Ginny Weasley" → False
         "Mr. Weasley" ↔ "Mrs. Weasley" → False
    3. At least one name is a single word → allow substring match if:
       - The shorter name is at least 3 characters
       - The shorter name is contained in the longer name's first word
       (prevents surname collisions like "Sley" matching "Ron Weasley")

    Catches these same-character variants:
      "Viv" ↔ "Viv the orc"            → True (single in first word)
      "Hermione" ↔ "Hermione Granger"  → True (same first word)
      "Flea" ↔ "Haflea"                → True (substring in single-word name)
      "Liz" ↔ "Elizabeth"              → True (nickname inside full name)
      "Tom" ↔ "Tom Riddle"             → True (single in first word)

    Correctly rejects:
      "Ron Weasley" ↔ "Ginny Weasley"  → False (different first words)
      "Sley" ↔ "Ron Weasley"           → False (sley not in "ron")
      "Otter" ↔ "Harry Potter"         → False (otter not in "harry")
      "Bob" ↔ "Robert"                 → False (no substring relationship)
    """
    a = a.lower().strip()
    b = b.lower().strip()
    if not a or not b:
        return False
    if a == b:
        return True

    a_parts = a.split()
    b_parts = b.split()
    if not a_parts or not b_parts:
        return False

    a_is_single = len(a_parts) == 1
    b_is_single = len(b_parts) == 1

    # Both multi-word: must share first word
    if not a_is_single and not b_is_single:
        return a_parts[0] == b_parts[0]

    # Both single-word: substring match (handles Flea/Haflea, Liz/Elizabeth)
    if a_is_single and b_is_single:
        sh, lo = (a, b) if len(a) <= len(b) else (b, a)
        return len(sh) >= 3 and sh in lo

    # One single, one multi: single must be in (or contain) the multi's first word.
    # This is the key safety rule — surnames are never the first word, so
    # "Sley" can never match "Ron Weasley" via this path.
    single = a if a_is_single else b
    multi_first = a_parts[0] if not a_is_single else b_parts[0]
    if len(single) < 3:
        return False
    if single == multi_first:
        return True
    if len(single) <= len(multi_first):
        return single in multi_first
    else:
        return multi_first in single

def _find_arc(cd, arcs):
    names = {cd["name"]}
    if "all" in cd:
        for n in cd["all"]:
            names.add(n); names.add(n.capitalize()); names.add(n.title())
    for n in names:
        if n in arcs: return arcs[n]
    al = {k.lower(): v for k, v in arcs.items()}
    for n in names:
        if n.lower() in al: return al[n.lower()]
    for ak, av in arcs.items():
        akl = ak.lower()
        for n in names:
            nl = n.lower()
            if len(nl) >= 3 and (nl in akl or akl in nl): return av
    return ""


# ── Prompt helpers ────────────────────────────────────────────────────────────

def _aggregate_themes_for_prompt(chunks):
    td = {}
    for c in chunks:
        for t in c.themes_detected:
            k = t.lower().strip()
            p = c.theme_prominences.get(k, 5)
            if k not in td: td[k] = {"name": k, "scores": [], "chunk_count": 0}
            td[k]["scores"].append(p); td[k]["chunk_count"] += 1
    return sorted(
        [{"name": d["name"], "avg": round(sum(d["scores"])/len(d["scores"]), 1),
          "chunks": d["chunk_count"]} for d in td.values()],
        key=lambda x: x["avg"], reverse=True)

def _aggregate_characters_for_prompt(chunks):
    cd = {}
    for c in chunks:
        for ch in c.characters_present:
            k = ch.name.lower().strip()
            if k not in cd:
                cd[k] = {"name": ch.name, "max_importance": 0, "chunk_count": 0}
            d = cd[k]
            d["max_importance"] = max(d["max_importance"], ch.importance)
            d["chunk_count"] += 1

    # Merge duplicates before sending to Pass 2
    keys = list(cd.keys())
    mi = {}
    for i in range(len(keys)):
        if keys[i] in mi: continue
        for j in range(i+1, len(keys)):
            if keys[j] in mi: continue
            if _same_char(keys[i], keys[j]):
                p = keys[i] if len(keys[i]) >= len(keys[j]) else keys[j]
                s = keys[j] if p == keys[i] else keys[i]
                while p in mi: p = mi[p]
                mi[s] = p
    rd = {}
    for k, d in cd.items():
        p = k
        while p in mi: p = mi[p]
        if p not in rd:
            pd = cd.get(p, d)
            rd[p] = {"name": pd["name"],
                     "max_importance": pd["max_importance"], "chunk_count": pd["chunk_count"]}
        if k != p:
            r = rd[p]
            r["max_importance"] = max(r["max_importance"], d["max_importance"])
            r["chunk_count"] += d["chunk_count"]
            if len(d["name"]) > len(r["name"]): r["name"] = d["name"]
    return sorted(rd.values(), key=lambda x: x["max_importance"], reverse=True)


def promote_memoir_protagonist(result, extraction):
    """
    For first-person memoirs/autobiographies, ensure the AUTHOR is ranked as
    the primary character.

    Background: in first-person memoirs the narrator says "I" throughout, so
    their own name appears far less often in the text than the names of people
    they talk about. Pure mention-frequency aggregation will rank a frequently-
    mentioned partner/parent/sibling above the narrator. This corrects that.

    Trigger conditions (ALL must hold):
      - book_type == non_fiction
      - non_fiction_info.sub_type == memoir_biography
      - holistic POV includes first_person (i.e. autobiography, not external bio)
      - extraction.author_guess is known

    Behavior:
      - If the author is already in result.characters, bump them to importance=10
        and re-sort.
      - If the author is NOT in the list, prepend them at importance=10 using
        info from the existing top character as a fallback for required fields.
      - All other characters keep their existing importance.

    This is a one-line semantic fix — it does NOT touch the mention-counting
    aggregator, which works correctly for fiction and external biographies.
    """
    # Guard: only for first-person memoirs
    nfi = getattr(result, "non_fiction_info", None)
    if nfi is None:
        return result
    if str(getattr(nfi, "sub_type", "")) not in ("memoir_biography",
                                                  "BookSubType.MEMOIR_BIOGRAPHY"):
        # str() because sub_type is an enum; check both common reprs
        sub_val = getattr(nfi.sub_type, "value", None) if hasattr(nfi, "sub_type") else None
        if sub_val != "memoir_biography":
            return result

    author = (extraction.author_guess or "").strip()
    if not author or author.lower() in ("unknown", ""):
        return result

    # Check POV — must include first_person to count as autobiography
    pov_list = getattr(result, "pov", None) or []
    pov_values = [getattr(p, "value", str(p)) for p in pov_list]
    if pov_values and "first_person" not in pov_values:
        # External biography (third-person about someone else) — don't promote
        return result

    # Find author in existing character list with a scoring approach so we
    # don't accidentally match a relative who shares the author's last name
    # (e.g. "Lynne Spears" should NOT match author "Britney Spears").
    author_norm = author.lower().strip()
    author_parts = [p for p in author_norm.split() if len(p) >= 2]
    author_last = author_parts[-1] if author_parts else ""
    author_first = author_parts[0] if author_parts else ""

    def match_score(char_name: str) -> int:
        """Higher score = stronger match. 0 = no match."""
        cn = char_name.lower().strip()
        # Exact full match wins
        if cn == author_norm:
            return 100
        # Both first and last name present, even if separated
        if (author_first and author_last
                and author_first in cn and author_last in cn):
            return 90
        # First name match alone (very specific — first names rarely shared
        # between author and the relatives they're writing about)
        if author_first and len(author_first) >= 3:
            for token in cn.split():
                if token == author_first:
                    return 80
        # Last name match alone — weakest, often shared with family members.
        # Only count it if no stronger match exists for ANY other character.
        # We return a low score here and let the caller decide.
        if author_last and author_last in cn.split():
            return 20
        return 0

    chars = list(result.characters or [])
    scored = [(i, match_score(c.name)) for i, c in enumerate(chars)]
    scored = [(i, s) for i, s in scored if s > 0]

    if scored:
        # Best match wins; if multiple share the top score, prefer the one
        # already most prominent (highest importance) — but only among ties.
        scored.sort(key=lambda x: (-x[1], -chars[x[0]].importance))
        best_idx, best_score = scored[0]

        # Last-name-only matches (score 20) are weak. If we have a weak match
        # AND there's no first-name signal anywhere, we'd rather inject a
        # fresh entry than promote a relative.
        if best_score >= 80:
            found_idx = best_idx
        else:
            # Treat as not found — fall through to injection
            found_idx = -1
    else:
        found_idx = -1

    if found_idx >= 0:
        # Bump existing
        chars[found_idx].importance = 10
        # Move to front
        chars.insert(0, chars.pop(found_idx))
    else:
        # Inject — borrow archetype/gender from top character if available,
        # but use defaults otherwise. The author IS the protagonist.
        from schemas import Character, CharacterArchetype
        chars.insert(0, Character(
            name=author,
            importance=10,
            gender="unknown",
            archetypes=[CharacterArchetype.OTHER],
            arc_summary=f"Author and first-person narrator of this memoir.",
            age_category=None,
        ))

    # Ensure no other character outranks the author now
    for c in chars[1:]:
        if c.importance > 9:
            c.importance = 9

    result.characters = chars[:8]  # Schema caps at 8
    return result

# ═══════════════════════════════════════════════════════════════════════════════
# FAST MODE — Slim Pass 1 (Haiku) + Rich Pass 2 (Sonnet)
# ═══════════════════════════════════════════════════════════════════════════════

def _build_slim_chunk_prompt(chunk, book_context, book_type="fiction"):
    """Tiny prompt for Haiku — just scores, names, flags, summary.

    Branches on book_type so non-fiction (memoirs, biographies, history) gets
    correct guidance: real people ARE the characters in a memoir, and the
    first-person narrator IS the author/subject.
    """
    flag_values = ", ".join(f'"{f.value}"' for f in ContentFlag)

    # Different character-extraction guidance per book type.
    # The fiction wording explicitly excludes real people (editors, authors)
    # because in novels those would be acknowledgments noise.
    # The non-fiction wording explicitly INCLUDES real people, and reminds
    # the model that in first-person memoirs the narrator "I" is the author.
    if book_type == "non_fiction":
        char_instruction = (
            'up to 10 named PEOPLE who appear in this section. Include the '
            'author/narrator if this is a memoir or first-person account '
            '(in memoirs the "I" voice IS a primary character). Include real '
            'people the text discusses by name. Exclude only meta-mentions '
            'like editors/publishers in acknowledgments-style asides.'
        )
    else:
        char_instruction = (
            "up to 10 character names — fictional characters who appear or "
            "are discussed in this section. Exclude real-world meta-mentions "
            "(editors, publishers, dedicatees)."
        )

    return f"""Score this text section. Respond ONLY with JSON, no preamble.

CONTEXT: {book_context}
BOOK TYPE: {book_type}
SECTION: {chunk.label} (~{chunk.word_count} words)

SCORING (1-10):
  tone: 1=light/cheerful, 5=balanced, 10=extremely dark/bleak
  readability: 1=very hard/dense, 5=average, 10=effortless/simple
  violence: 1=none, 5=moderate fights, 10=extreme gore
  pace: 1=very slow, 5=moderate, 10=relentless action
  worldbuilding: 1=no setting detail, 5=moderate, 10=exhaustive
  humor: 1=none, 5=regular comedy, 10=maximum comedy
  romance: 1=none, 5=notable romance, 10=love story is everything

JSON:
{{
  "chunk_index": {chunk.index},
  "chunk_label": "{chunk.label}",
  "word_count": {chunk.word_count},
  "tone": int,
  "readability": int,
  "violence": int,
  "pace": int,
  "worldbuilding": int,
  "humor": int,
  "romance": int,
  "character_names": ["{char_instruction}"],
  "content_flags": [{flag_values}],
  "summary": "One sentence summary of what happens in this section."
}}

CONTENT FLAG RULES — IMPORTANT:
- Only include flags for content that is ACTUALLY PRESENT in this section
  (genuinely shown or discussed at meaningful length — not just hinted at).
- A passing one-sentence mention is NOT enough to flag.
- Example: a character pouring a glass of wine is NOT "substance_abuse".
  A character struggling with addiction throughout the section IS.
- Example: a brief tense argument is NOT "graphic_violence". An on-page
  fight, attack, or violent death IS.
- If nothing applies, return [] or ["none"].

TEXT:
---
{chunk.text[:100000]}
---

JSON:"""


def _sanitize_slim_chunk_data(data):
    """Sanitize slim chunk data."""
    # Score fields: clamp valid numbers to 1–10; default to 5 when the model
    # returned null, omitted the field, or returned a non-numeric value
    # (happens on tiny/empty chunks the model can't meaningfully score).
    for f in ["tone", "readability", "violence", "pace",
              "worldbuilding", "humor", "romance"]:
        val = data.get(f)
        if isinstance(val, bool):
            # bool is a subclass of int — reject it explicitly
            data[f] = 5
        elif isinstance(val, (int, float)):
            data[f] = max(1, min(10, int(val)))
        else:
            data[f] = 5

    if "character_names" in data and isinstance(data["character_names"], list):
        data["character_names"] = [n.strip() for n in data["character_names"]
                                    if isinstance(n, str) and n.strip() and len(n.strip()) >= 2][:10]
    else:
        data["character_names"] = []

    if "content_flags" in data:
        data["content_flags"] = _sanitize_enum_list(
            data["content_flags"], _VALID_FLAGS, _FLAG_MAP, "none")
    else:
        data["content_flags"] = ["none"]

    if "summary" not in data or not isinstance(data.get("summary"), str):
        data["summary"] = ""

    return data


def _build_detection_prompt(opening_text: str) -> str:
    """
    Tiny prompt that asks the model to classify a book sample as fiction
    or non-fiction. Used by AnalysisClient.detect_book_type.

    Output: single word — 'fiction' or 'non_fiction'.

    The prompt is deliberately simple. Modern Claude models are very reliable
    on this binary distinction. We accept either phrasing variant in parsing.
    """
    return f"""Read this opening passage from a book. Decide if the book is FICTION or NON-FICTION.

Guidelines:
- Fiction: novels, novellas, short story collections, narrative invented stories
- Non-fiction: memoirs, biographies, history, self-help, science writing, philosophy,
  business, cookbooks, travel writing, true crime, journalism, academic works
- Memoirs and autobiographies are NON-FICTION (they describe real events)
- Historical novels and biographical novels are FICTION (they invent dialogue/scenes)
- If uncertain, lean toward FICTION (the more common case)

Respond with EXACTLY ONE WORD: "fiction" or "non_fiction". No other text.

PASSAGE:
---
{opening_text}
---

ANSWER:"""


# ═══════════════════════════════════════════════════════════════════════════════
# NON-FICTION PASS 2 — common-core fields (Session C)
# ═══════════════════════════════════════════════════════════════════════════════

def _build_nonfic_holistic_prompt(pass1_analyses, extraction):
    """
    Pass 2 prompt for non-fiction books. Returns JSON with BOTH:
      - The 8 NonFictionInfo fields (thesis, target_audience, prerequisites,
        structure_type, tone_register, practical_vs_theoretical, conclusion_type,
        sub_type)
      - The universal fields needed to construct a BookAnalysis (genre,
        sub_genres, setting, reading_experience, categories, age_target,
        content_flags, overall_summary, ranked_themes)

    Skips fiction-only fields entirely (characters, character_arcs, POV,
    sad_ending, cliffhanger, humor_types).

    This is the unified non-fiction Pass 2 — running this means we no longer
    need to run the fiction Pass 2 on non-fiction books.

    Works with both ChunkAnalysis (quality mode) and SlimChunkAnalysis (fast/faster
    mode) — only reads `.label` and `.summary` from each chunk analysis.
    """
    # Build a compact chapter spine: label + summary per chunk
    chunk_spine_lines = []
    char_freq: dict[str, dict] = {}  # lowercase name → {"name": original, "count": int}
    for sa in pass1_analyses:
        label = getattr(sa, "label", None) or getattr(sa, "chunk_label", "?")
        summary = getattr(sa, "summary", "") or ""
        chunk_spine_lines.append(f"- {label}: {summary[:300]}")
        # Aggregate character names across chunks (works for SlimChunkAnalysis
        # and ChunkAnalysis — fall back to characters_present in quality mode).
        names = getattr(sa, "character_names", None)
        if names is None:
            present = getattr(sa, "characters_present", []) or []
            names = [getattr(c, "name", "") for c in present]
        for n in (names or []):
            if isinstance(n, str) and n.strip():
                key = n.lower().strip()
                if key not in char_freq:
                    char_freq[key] = {"name": n.strip(), "count": 0}
                char_freq[key]["count"] += 1
    chunk_spine = "\n".join(chunk_spine_lines[:60])  # cap at 60 chapters for safety

    # Top character candidates (memoir/biography/history will use these;
    # cookbook/self-help/business prompt is told to return empty).
    top_chars = sorted(char_freq.values(), key=lambda x: x["count"], reverse=True)[:15]
    char_names_str = ", ".join(f'"{c["name"]}"' for c in top_chars) or "(none detected)"
    arch_values = ", ".join(f'"{a.value}"' for a in CharacterArchetype)

    # Compact metadata header
    title = getattr(extraction, "title", "") or "(unknown title)"
    author = getattr(extraction, "author", "") or "(unknown author)"
    word_count = getattr(extraction, "total_words", 0)

    # ── Valid enum values for the prompt ──
    # NonFictionInfo enums
    subtypes = ", ".join(f'"{s.value}"' for s in BookSubType)
    audiences = ", ".join(f'"{a.value}"' for a in TargetAudience)
    structures = ", ".join(f'"{s.value}"' for s in StructureType)
    tones = ", ".join(f'"{t.value}"' for t in ToneRegister)
    conclusions = ", ".join(f'"{c.value}"' for c in ConclusionType)
    # Universal enums (subset relevant to non-fiction)
    # Filter Genre to non-fiction-friendly values, but allow all so the model can pick
    non_fic_genres = ", ".join(f'"{g.value}"' for g in [
        Genre.BIOGRAPHIES_AND_MEMOIRS, Genre.BUSINESS_AND_ECONOMICS,
        Genre.COOKBOOKS_AND_FOOD, Genre.HEALTH_FITNESS_AND_WELLNESS,
        Genre.HISTORY, Genre.NATURE_AND_ENVIRONMENT,
        Genre.PHILOSOPHY_AND_RELIGION, Genre.SCIENCE_AND_TECHNOLOGY,
        Genre.SELF_HELP, Genre.SOCIAL_SCIENCES, Genre.SPORTS_AND_OUTDOORS,
        Genre.TRAVEL, Genre.TRUE_CRIME, Genre.ESSAYS_AND_ANTHOLOGIES,
        Genre.ARTS_AND_PHOTOGRAPHY, Genre.CRAFTS_HOBBIES_AND_HOME,
        Genre.EDUCATION_AND_REFERENCE, Genre.HUMOR, Genre.LAW_AND_POLITICS,
        Genre.PARENTING_AND_FAMILY, Genre.RELIGIOUS_AND_INSPIRATIONAL,
        Genre.OTHER,
    ])
    reading_exps = ", ".join(f'"{r.value}"' for r in ReadingExperience)
    content_flag_values = ", ".join(f'"{c.value}"' for c in ContentFlag)
    time_periods = ", ".join(f'"{t.value}"' for t in TimePeriod)
    setting_types = ", ".join(f'"{s.value}"' for s in SettingType)

    # ── Addendum-specific enums (Session E) ──
    commitment_levels = ", ".join(f'"{c.value}"' for c in CommitmentLevel)
    narrative_shapes = ", ".join(f'"{n.value}"' for n in NarrativeShape)
    subject_rels = ", ".join(f'"{s.value}"' for s in SubjectRelationship)
    hist_perspectives = ", ".join(f'"{h.value}"' for h in HistoricalPerspective)
    acad_levels = ", ".join(f'"{a.value}"' for a in AcademicLevel)
    phil_foci = ", ".join(f'"{p.value}"' for p in PhilosophyFocus)
    biz_roles = ", ".join(f'"{b.value}"' for b in BusinessAudienceRole)
    health_bases = ", ".join(f'"{h.value}"' for h in HealthEvidenceBasis)
    recipe_diffs = ", ".join(f'"{r.value}"' for r in RecipeDifficulty)
    cookbook_purposes = ", ".join(f'"{c.value}"' for c in CookbookPurpose)
    travel_styles = ", ".join(f'"{t.value}"' for t in TravelStyle)
    tc_case_types = ", ".join(f'"{t.value}"' for t in TrueCrimeCaseType)
    tc_resolutions = ", ".join(f'"{t.value}"' for t in TrueCrimeResolution)
    tc_perspectives = ", ".join(f'"{t.value}"' for t in TrueCrimePerspective)

    # Master lists for themes / sub_genres / categories
    theme_list = "\n".join(f'  - "{t}"' for t in MASTER_THEMES)
    sub_genre_list = "\n".join(f'  - "{s}"' for s in MASTER_SUB_GENRES)
    category_list = "\n".join(f'  - "{c}"' for c in MASTER_CATEGORIES)

    opening = (extraction.opening_text or "")[:6000]
    closing = (extraction.closing_text or "")[:4000]

    return f"""Analyze this non-fiction book. Respond ONLY with JSON, no preamble.

BOOK: "{title}" by {author} ({word_count:,} words)

CHAPTER SPINE (label + 1-sentence summary):
{chunk_spine}

OPENING SAMPLE:
---
{opening}
---

CLOSING SAMPLE:
---
{closing}
---

Fill in this JSON. Use exact canonical values for enum fields.

{{
  "title": "the book's title (use the BOOK header above if you can't infer better)",
  "author": "the book's author (use the BOOK header above if you can't infer better)",
  "thesis": "1-2 sentence statement of the book's central argument, claim, or stated purpose. Different from a summary — this is what the author wants the reader to take away.",
  "target_audience": "pick one from: {audiences}",
  "prerequisites": ["list of things readers should know first — empty list if none required"],
  "structure_type": "pick one from: {structures}",
  "tone_register": ["pick 1-3 from: {tones}"],
  "practical_vs_theoretical": <int 1-10, where 1 = pure theory, 10 = pure actionable how-to>,
  "conclusion_type": "pick one from: {conclusions}",
  "sub_type": "pick the BEST single sub-type from: {subtypes}",

  "self_help": null,
  "memoir_biography": null,
  "history_narrative": null,
  "academic_textbook": null,
  "popular_science": null,
  "philosophy_religion": null,
  "business_economics": null,
  "health_fitness": null,
  "cooking_food": null,
  "travel_nature": null,
  "true_crime": null,

  "genre": "pick one from: {non_fic_genres}",
  "sub_genres": ["1-3 specific sub-genres from the master list below"],
  "ranked_themes": ["3-7 themes from the master list, most prominent first"],
  "categories": ["exactly 3 discovery categories from the master list below"],
  "reading_experience": ["1-4 experience tags from: {reading_exps}"],
  "content_flags": ["any applicable from: {content_flag_values}. Use [\\"none\\"] if none apply."],
  "setting": {{
    "primary_location": "specific location, region, or topic-domain. Empty string if not applicable.",
    "time_period": "pick one from: {time_periods}",
    "setting_type": "pick one from: {setting_types}",
    "real_or_fictional": "always 'real' for non-fiction",
    "additional_locations": []
  }},
  "age_target": <int 1-10, using the standard rubric: 1=very young (Goodnight Moon), 5=YA/adult (Hunger Games), 10=mature adult (A Little Life). For non-fiction: 1-3 = picture books / children's non-fic, 4-6 = general adult reader, 7-10 = mature/explicit content, trauma memoirs, graphic histories. Higher = more mature.>,
  "overall_summary": "2-3 sentence factual summary of what the book covers.",

  "ranked_characters": ["top 8 named PEOPLE by overall importance to this book. ONLY fill for memoir_biography, history_narrative, true_crime, or other character-driven non-fiction. Return [] for self_help, business_economics, cooking_food, philosophy_religion, popular_science, academic_textbook, health_fitness, travel_nature (no real characters to rank)."],
  "character_arcs": {{
    "PersonName": "1-3 sentence summary of this person's role/journey/significance in the book. Only fill when ranked_characters is non-empty."
  }},
  "character_genders": {{
    "PersonName": "male|female|non-binary|unknown"
  }},
  "character_archetypes": {{
    "PersonName": ["1-3 archetypes from: {arch_values}"]
  }},
  "character_ages": {{
    "PersonName": "child|teen|young_adult|adult|elderly|ageless|null"
  }}
}}

GUIDELINES — CHARACTER FIELDS (memoir/biography/history/true_crime ONLY):
- Pass 1 detected these candidate names: [{char_names_str}]
- ranked_characters: pick the TOP 8 most important real people, ranked by significance to the book.
  - Memoir/autobiography: the author/narrator is usually #1.
  - Biography: the subject is #1.
  - For OTHER non-fiction sub_types (cookbook, self-help, business, etc.), return [] —
    those books have no real "characters" in this sense.
- Use the EXACT names from the candidate list (or canonical full names if ambiguous).
- character_arcs / character_genders / character_archetypes / character_ages must have
  one entry per name in ranked_characters. Leave as {{}} if ranked_characters is [].
- Each arc must be 1-3 real sentences. Never empty or generic.

GUIDELINES — NON-FICTION-SPECIFIC FIELDS:
- thesis: be specific. "About productivity" is bad; "Habits are formed by repeating small actions consistently over time" is good.
- prerequisites: only include if the book explicitly assumes prior knowledge. Most general-audience books need none.
- structure_type:
  - linear_argument: sequential chapters building one argument (most common)
  - episodic_chapters: standalone chapters/essays, any order
  - case_studies: organized around real examples
  - reference: meant to be looked up by topic
  - workbook: contains exercises/prompts
  - mixed: combines multiple structures
- tone_register: 1-3 values describing the writing VOICE (not topic).
- practical_vs_theoretical: tactics book = 9-10; philosophy = 1-3; pop-science = 3-6.
- conclusion_type: how does the book end?
- sub_type: pick the SINGLE best fit. Use "other" only if truly no fit (poetry, art book).

GUIDELINES — SUB-TYPE ADDENDUMS:
Fill in EXACTLY ONE addendum object that matches the chosen sub_type. Leave all
other addendum fields as null. If sub_type is "other", leave all addendums null.

If sub_type == "self_help":
  {{
    "skill_or_outcome": "<short phrase: what the reader will be able to do>",
    "has_exercises": <true/false>,
    "commitment_level": "<one of: {commitment_levels}>"
  }}

If sub_type == "memoir_biography":
  {{
    "life_period_covered": "<short phrase: e.g. 'childhood through college'>",
    "narrative_shape": "<one of: {narrative_shapes}>",
    "subject_relationship": "<one of: {subject_rels}>"
  }}

If sub_type == "history_narrative":
  {{
    "historical_period": "<the time period the book focuses on>",
    "geographic_focus": "<region, country, or area covered>",
    "perspective_centered": "<one of: {hist_perspectives}>"
  }}

If sub_type == "academic_textbook":
  {{
    "discipline": "<field of study, e.g. 'biochemistry'>",
    "level": "<one of: {acad_levels}>",
    "has_exercises": <true/false>
  }}

If sub_type == "popular_science":
  {{
    "scientific_domain": "<branch of science, e.g. 'cosmology'>",
    "accessibility_score": <int 1-10, 1=requires deep prior knowledge, 10=fully accessible>,
    "is_cutting_edge": <true/false: true if covers recent or developing science>
  }}

If sub_type == "philosophy_religion":
  {{
    "tradition": "<tradition/school, e.g. 'analytic philosophy', 'Zen Buddhism'>",
    "focus": "<one of: {phil_foci}>"
  }}

If sub_type == "business_economics":
  {{
    "domain": "<specific area, e.g. 'leadership', 'behavioral economics'>",
    "audience_role": "<one of: {biz_roles}>"
  }}

If sub_type == "health_fitness":
  {{
    "focus_area": "<specific area, e.g. 'nutrition', 'sleep', 'strength training'>",
    "evidence_basis": "<one of: {health_bases}>"
  }}

If sub_type == "cooking_food":
  {{
    "cuisine_type": "<cuisine/tradition, e.g. 'French', 'plant-based'>",
    "recipe_difficulty": "<one of: {recipe_diffs}>",
    "book_purpose": "<one of: {cookbook_purposes}>"
  }}

If sub_type == "travel_nature":
  {{
    "location_focus": "<primary location or region>",
    "travel_style": "<one of: {travel_styles}>"
  }}

If sub_type == "true_crime":
  {{
    "case_type": "<one of: {tc_case_types}>",
    "resolution": "<one of: {tc_resolutions}>",
    "investigation_perspective": "<one of: {tc_perspectives}>"
  }}

GUIDELINES — UNIVERSAL FIELDS:
- genre: pick the genre that best describes the BOOK CATEGORY (not the sub_type).
  A memoir = biographies_and_memoirs. A productivity book = self_help.
- ranked_themes: 3-7 themes the book engages with. Use the master list exactly.
  Non-fiction themes can include things like 'memory', 'identity', 'power', 'community',
  'science_and_discovery', 'work_and_career', 'personal_growth'.
- setting: For MEMOIRS, BIOGRAPHIES, HISTORY, TRAVEL, TRUE CRIME — give real setting
  (e.g. memoir of growing up in 1980s Detroit → primary_location="Detroit",
  time_period="late_20th_century", setting_type="urban"). For SELF-HELP, PHILOSOPHY,
  BUSINESS, COOKING, HEALTH — use defaults (primary_location="",
  time_period="timeless_or_unspecified", setting_type="domestic").
- age_target: STANDARD scale — 1=very young readers (Goodnight Moon), 5=teen/YA (Hunger Games),
  10=mature adult (A Little Life). For non-fic: 1-3 = children's non-fic; 4-6 = general adult;
  7-10 = mature content (trauma memoirs, graphic histories, explicit material). HIGHER = MORE MATURE.
- content_flags: apply to non-fiction too. War histories have violence flags;
  trauma memoirs have abuse/death; medical books may have explicit_health_content.
- overall_summary: factual, NOT marketing copy. 2-3 sentences.

MASTER THEMES (use EXACT strings):
{theme_list}

MASTER SUB_GENRES (use EXACT strings):
{sub_genre_list}

MASTER CATEGORIES (use EXACT strings, pick 3):
{category_list}

JSON:"""


def _sanitize_self_help_addendum(raw):
    """
    Sanitize a self_help addendum dict from the Pass 2 response.

    Returns either:
      - A dict ready to construct SelfHelpAddendum(**dict), OR
      - None if raw is null/missing/not a dict (signals "no addendum")

    All fields are Optional, so any individual field that can't be cleaned
    falls back to None — the addendum object itself is still constructed.
    """
    if not isinstance(raw, dict):
        return None

    out = {}

    # skill_or_outcome: short string
    sko = raw.get("skill_or_outcome")
    if isinstance(sko, str) and sko.strip():
        out["skill_or_outcome"] = sko.strip()[:500]
    else:
        out["skill_or_outcome"] = None

    # has_exercises: bool
    he = raw.get("has_exercises")
    if isinstance(he, bool):
        out["has_exercises"] = he
    elif isinstance(he, str):
        s = he.strip().lower()
        if s in ("true", "yes", "1"):
            out["has_exercises"] = True
        elif s in ("false", "no", "0"):
            out["has_exercises"] = False
        else:
            out["has_exercises"] = None
    else:
        out["has_exercises"] = None

    # commitment_level: enum (single)
    cl = raw.get("commitment_level")
    if isinstance(cl, str) and cl.strip():
        cleaned = _sanitize_single(cl, _VALID_COMMITMENT, _COMMITMENT_MAP, None)
        out["commitment_level"] = cleaned  # may be None if no match
    else:
        out["commitment_level"] = None

    return out


# ── Shared micro-helpers for addendum sanitization (Session E) ─────────────
# These are tiny no-abstraction helpers that just reduce typo risk across
# the 11 addendum sanitizers below.

def _addendum_str(raw, key, max_len=500):
    """Extract a string field from an addendum dict; None if missing/empty/wrong type."""
    v = raw.get(key)
    if isinstance(v, str) and v.strip():
        return v.strip()[:max_len]
    return None


def _addendum_bool(raw, key):
    """Extract a bool field; accept 'true'/'false' strings; None on bad input."""
    v = raw.get(key)
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("true", "yes", "1"):
            return True
        if s in ("false", "no", "0"):
            return False
    return None


def _addendum_enum(raw, key, valid_set, synonym_map):
    """Extract and sanitize an enum field; None on missing/invalid."""
    v = raw.get(key)
    if isinstance(v, str) and v.strip():
        return _sanitize_single(v, valid_set, synonym_map, None)
    return None


def _addendum_int(raw, key, lo=1, hi=10):
    """Extract an int field clamped to [lo, hi]; None on bad input."""
    v = raw.get(key)
    if isinstance(v, bool):  # bool is subclass of int — exclude
        return None
    if isinstance(v, (int, float)):
        return max(lo, min(hi, int(v)))
    if isinstance(v, str) and v.strip().lstrip("-").isdigit():
        return max(lo, min(hi, int(v.strip())))
    return None


def _sanitize_memoir_biography_addendum(raw):
    """Sanitize memoir/biography addendum. Fields: life_period_covered (str),
    narrative_shape (enum), subject_relationship (enum)."""
    if not isinstance(raw, dict):
        return None
    return {
        "life_period_covered": _addendum_str(raw, "life_period_covered"),
        "narrative_shape": _addendum_enum(raw, "narrative_shape",
            _VALID_NARRATIVE, _NARRATIVE_SHAPE_MAP),
        "subject_relationship": _addendum_enum(raw, "subject_relationship",
            _VALID_SUBJECT_REL, _SUBJECT_REL_MAP),
    }


def _sanitize_history_narrative_addendum(raw):
    """Sanitize history/narrative addendum. Fields: historical_period (str),
    geographic_focus (str), perspective_centered (enum)."""
    if not isinstance(raw, dict):
        return None
    return {
        "historical_period": _addendum_str(raw, "historical_period"),
        "geographic_focus": _addendum_str(raw, "geographic_focus"),
        "perspective_centered": _addendum_enum(raw, "perspective_centered",
            _VALID_HIST_PERSP, _HIST_PERSP_MAP),
    }


def _sanitize_academic_textbook_addendum(raw):
    """Sanitize academic textbook addendum. Fields: discipline (str),
    level (enum), has_exercises (bool)."""
    if not isinstance(raw, dict):
        return None
    return {
        "discipline": _addendum_str(raw, "discipline"),
        "level": _addendum_enum(raw, "level", _VALID_ACAD_LEVEL, _ACAD_LEVEL_MAP),
        "has_exercises": _addendum_bool(raw, "has_exercises"),
    }


def _sanitize_popular_science_addendum(raw):
    """Sanitize popular science addendum. Fields: scientific_domain (str),
    accessibility_score (int 1-10), is_cutting_edge (bool)."""
    if not isinstance(raw, dict):
        return None
    return {
        "scientific_domain": _addendum_str(raw, "scientific_domain"),
        "accessibility_score": _addendum_int(raw, "accessibility_score"),
        "is_cutting_edge": _addendum_bool(raw, "is_cutting_edge"),
    }


def _sanitize_philosophy_religion_addendum(raw):
    """Sanitize philosophy/religion addendum. Fields: tradition (str), focus (enum)."""
    if not isinstance(raw, dict):
        return None
    return {
        "tradition": _addendum_str(raw, "tradition"),
        "focus": _addendum_enum(raw, "focus", _VALID_PHIL_FOCUS, _PHIL_FOCUS_MAP),
    }


def _sanitize_business_economics_addendum(raw):
    """Sanitize business/economics addendum. Fields: domain (str), audience_role (enum)."""
    if not isinstance(raw, dict):
        return None
    return {
        "domain": _addendum_str(raw, "domain"),
        "audience_role": _addendum_enum(raw, "audience_role",
            _VALID_BIZ_ROLE, _BIZ_ROLE_MAP),
    }


def _sanitize_health_fitness_addendum(raw):
    """Sanitize health/fitness addendum. Fields: focus_area (str), evidence_basis (enum)."""
    if not isinstance(raw, dict):
        return None
    return {
        "focus_area": _addendum_str(raw, "focus_area"),
        "evidence_basis": _addendum_enum(raw, "evidence_basis",
            _VALID_HEALTH_BASIS, _HEALTH_BASIS_MAP),
    }


def _sanitize_cooking_food_addendum(raw):
    """Sanitize cooking/food addendum. Fields: cuisine_type (str),
    recipe_difficulty (enum), book_purpose (enum)."""
    if not isinstance(raw, dict):
        return None
    return {
        "cuisine_type": _addendum_str(raw, "cuisine_type"),
        "recipe_difficulty": _addendum_enum(raw, "recipe_difficulty",
            _VALID_RECIPE_DIFF, _RECIPE_DIFF_MAP),
        "book_purpose": _addendum_enum(raw, "book_purpose",
            _VALID_COOKBOOK_PURPOSE, _COOKBOOK_PURPOSE_MAP),
    }


def _sanitize_travel_nature_addendum(raw):
    """Sanitize travel/nature addendum. Fields: location_focus (str), travel_style (enum)."""
    if not isinstance(raw, dict):
        return None
    return {
        "location_focus": _addendum_str(raw, "location_focus"),
        "travel_style": _addendum_enum(raw, "travel_style",
            _VALID_TRAVEL_STYLE, _TRAVEL_STYLE_MAP),
    }


def _sanitize_true_crime_addendum(raw):
    """Sanitize true crime addendum. Fields: case_type (enum), resolution (enum),
    investigation_perspective (enum)."""
    if not isinstance(raw, dict):
        return None
    return {
        "case_type": _addendum_enum(raw, "case_type", _VALID_TC_CASE, _TC_CASE_MAP),
        "resolution": _addendum_enum(raw, "resolution",
            _VALID_TC_RESOLUTION, _TC_RESOLUTION_MAP),
        "investigation_perspective": _addendum_enum(raw, "investigation_perspective",
            _VALID_TC_PERSPECTIVE, _TC_PERSPECTIVE_MAP),
    }


def _sanitize_nonfic_data(data):
    """
    Validate and normalize the NonFictionInfo-specific fields of the non-fiction
    Pass 2 JSON output. Produces safe defaults when the model returns
    invalid/missing values, so NonFictionInfo construction never fails.

    Returns a dict ready to splat into NonFictionInfo(**data).

    Handles common-core fields plus sub-type addendums (Session E).
    Each addendum is only populated if sub_type matches; otherwise stays None.
    """
    if not isinstance(data, dict):
        data = {}
    # Work on a copy so we don't mutate the caller's dict
    data = dict(data)

    # thesis (required string)
    thesis = data.get("thesis")
    if not isinstance(thesis, str) or not thesis.strip():
        data["thesis"] = "(Unable to determine thesis from text)"
    else:
        data["thesis"] = thesis.strip()[:1000]  # cap length

    # target_audience (single enum, fallback to general_reader)
    data["target_audience"] = _sanitize_single(
        data.get("target_audience", ""), _VALID_AUDIENCE, _AUDIENCE_MAP, "general_reader")

    # prerequisites (list of strings, empty list if absent/invalid)
    prereqs = data.get("prerequisites")
    if isinstance(prereqs, list):
        data["prerequisites"] = [p.strip() for p in prereqs if isinstance(p, str) and p.strip()][:10]
    else:
        data["prerequisites"] = []

    # structure_type (single enum, fallback to mixed)
    data["structure_type"] = _sanitize_single(
        data.get("structure_type", ""), _VALID_STRUCTURE, _STRUCTURE_MAP, "mixed")

    # tone_register (list of 1-3 enum values; ensure at least one)
    tone_list = data.get("tone_register")
    if isinstance(tone_list, list):
        sanitized = _sanitize_enum_list(tone_list, _VALID_TONE_REG, _TONE_REG_MAP, "conversational")
        data["tone_register"] = sanitized[:3]
    elif isinstance(tone_list, str):
        single = _sanitize_single(tone_list, _VALID_TONE_REG, _TONE_REG_MAP, "conversational")
        data["tone_register"] = [single]
    else:
        data["tone_register"] = ["conversational"]
    if not data["tone_register"]:
        data["tone_register"] = ["conversational"]

    # practical_vs_theoretical (int 1-10, fallback to 5)
    pvt = data.get("practical_vs_theoretical")
    if isinstance(pvt, (int, float)):
        data["practical_vs_theoretical"] = max(1, min(10, int(pvt)))
    elif isinstance(pvt, str) and pvt.strip().isdigit():
        data["practical_vs_theoretical"] = max(1, min(10, int(pvt.strip())))
    else:
        data["practical_vs_theoretical"] = 5

    # conclusion_type (single enum, fallback to open_ended)
    data["conclusion_type"] = _sanitize_single(
        data.get("conclusion_type", ""), _VALID_CONCLUSION, _CONCLUSION_MAP, "open_ended")

    # sub_type (single enum, fallback to other)
    data["sub_type"] = _sanitize_single(
        data.get("sub_type", ""), _VALID_SUBTYPE, _SUBTYPE_MAP, "other")

    # ── Sub-type addendums (Session E) ──
    # Each addendum is only populated when sub_type matches AND the model
    # returned a dict for it. Otherwise stays None (the schema default).
    # The model is instructed to use null for non-matching addendums.
    # We always set all 11 addendum keys (to None or the sanitized data) so
    # the **data splat into NonFictionInfo gives an explicit None for unused
    # slots rather than relying on the schema default.

    # Map sub_type → (addendum_key, sanitizer_function)
    _ADDENDUM_DISPATCH = {
        "self_help":            ("self_help",            _sanitize_self_help_addendum),
        "memoir_biography":     ("memoir_biography",     _sanitize_memoir_biography_addendum),
        "history_narrative":    ("history_narrative",    _sanitize_history_narrative_addendum),
        "academic_textbook":    ("academic_textbook",    _sanitize_academic_textbook_addendum),
        "popular_science":      ("popular_science",      _sanitize_popular_science_addendum),
        "philosophy_religion":  ("philosophy_religion",  _sanitize_philosophy_religion_addendum),
        "business_economics":   ("business_economics",   _sanitize_business_economics_addendum),
        "health_fitness":       ("health_fitness",       _sanitize_health_fitness_addendum),
        "cooking_food":         ("cooking_food",         _sanitize_cooking_food_addendum),
        "travel_nature":        ("travel_nature",        _sanitize_travel_nature_addendum),
        "true_crime":           ("true_crime",           _sanitize_true_crime_addendum),
        # "other" intentionally absent — no addendum
    }
    # All possible addendum keys (always written as None unless matched)
    _ALL_ADDENDUM_KEYS = [v[0] for v in _ADDENDUM_DISPATCH.values()]

    matched_sub_type = data["sub_type"]
    if matched_sub_type in _ADDENDUM_DISPATCH:
        addendum_key, sanitizer_fn = _ADDENDUM_DISPATCH[matched_sub_type]
        sanitized_addendum = sanitizer_fn(data.get(addendum_key))
        # Clear all addendum slots, then set the matched one
        for k in _ALL_ADDENDUM_KEYS:
            data[k] = None
        data[addendum_key] = sanitized_addendum
    else:
        # sub_type is "other" (or unexpected) — no addendum applies
        for k in _ALL_ADDENDUM_KEYS:
            data[k] = None

    # Strip out any keys not in the NonFictionInfo schema so the **data splat
    # doesn't choke on unexpected fields
    allowed = {"thesis", "target_audience", "prerequisites", "structure_type",
               "tone_register", "practical_vs_theoretical", "conclusion_type",
               "sub_type"} | set(_ALL_ADDENDUM_KEYS)
    return {k: v for k, v in data.items() if k in allowed}


def _sanitize_nonfic_universal_data(data, extraction):
    """
    Validate and normalize the UNIVERSAL fields of the non-fiction Pass 2 output.
    Returns a dict ready to construct a HolisticAnalysis (fiction-only fields
    are populated with empty/default values).

    Mirrors the relevant subset of _sanitize_holistic_data. Fiction-only fields
    get explicit empty defaults so the resulting HolisticAnalysis still validates.
    """
    if not isinstance(data, dict):
        data = {}
    data = dict(data)

    # title / author — fall back to extraction metadata if missing.
    # ExtractionResult uses title_guess / author_guess (not title / author).
    if not isinstance(data.get("title"), str) or not data.get("title", "").strip():
        data["title"] = getattr(extraction, "title_guess", "") or "Unknown Title"
    if not isinstance(data.get("author"), str) or not data.get("author", "").strip():
        data["author"] = getattr(extraction, "author_guess", "") or "Unknown Author"

    # book_type: always non_fiction (we know this from detection)
    data["book_type"] = "non_fiction"

    # genre (single enum, fallback to "other")
    g = data.get("genre")
    if isinstance(g, str):
        data["genre"] = _sanitize_single(g, _VALID_GENRES, _GENRE_MAP, "other")
    else:
        data["genre"] = "other"

    # sub_genres (normalize against master list, dedupe, cap at 4)
    if "sub_genres" in data and isinstance(data["sub_genres"], list):
        norm = [_normalize_sub_genre(sg) for sg in data["sub_genres"] if isinstance(sg, str)]
        seen_sg = set()
        deduped = []
        for s in norm:
            if s and s.lower() not in seen_sg:
                seen_sg.add(s.lower())
                deduped.append(s)
        data["sub_genres"] = deduped[:4] or ["nonfiction"]
    else:
        data["sub_genres"] = ["nonfiction"]

    # ranked_themes (normalize against master, dedupe)
    if "ranked_themes" in data and isinstance(data["ranked_themes"], list):
        normalized = [_normalize_theme(t) for t in data["ranked_themes"] if isinstance(t, str)]
        seen_t = set()
        deduped = []
        for t in normalized:
            if t and t.lower() not in seen_t:
                seen_t.add(t.lower())
                deduped.append(t)
        data["ranked_themes"] = deduped[:10]
    else:
        data["ranked_themes"] = []

    # categories
    if "categories" in data and isinstance(data["categories"], list):
        norm = [_normalize_category(c) for c in data["categories"] if isinstance(c, str)]
        data["categories"] = [c for c in norm if c][:3]
    if not data.get("categories"):
        data["categories"] = ["standalone"]

    # reading_experience
    if "reading_experience" in data and isinstance(data["reading_experience"], list):
        exp = _sanitize_enum_list(
            data["reading_experience"], _VALID_EXP, _EXP_MAP, "thought_provoking")
        data["reading_experience"] = exp[:4]
    else:
        data["reading_experience"] = ["thought_provoking"]
    if not data["reading_experience"]:
        data["reading_experience"] = ["thought_provoking"]

    # content_flags
    if "content_flags" in data and isinstance(data["content_flags"], list):
        data["content_flags"] = _sanitize_enum_list(
            data["content_flags"], _VALID_FLAGS, _FLAG_MAP, "none")
    else:
        data["content_flags"] = ["none"]
    if not data["content_flags"]:
        data["content_flags"] = ["none"]

    # setting (sanitize sub-fields)
    if "setting" in data and isinstance(data["setting"], dict):
        s = data["setting"]
        s["primary_location"] = s.get("primary_location", "") if isinstance(s.get("primary_location"), str) else ""
        s["time_period"] = _sanitize_single(
            s.get("time_period", ""), _VALID_TIME, _TIME_MAP, "timeless_or_unspecified")
        s["setting_type"] = _sanitize_single(
            s.get("setting_type", ""), _VALID_SETTING, _SETTING_MAP, "domestic")
        s["real_or_fictional"] = s.get("real_or_fictional", "real") if isinstance(s.get("real_or_fictional"), str) else "real"
        addl = s.get("additional_locations", [])
        s["additional_locations"] = [a for a in addl if isinstance(a, str)] if isinstance(addl, list) else []
    else:
        data["setting"] = {
            "primary_location": "",
            "time_period": "timeless_or_unspecified",
            "setting_type": "domestic",
            "real_or_fictional": "real",
            "additional_locations": [],
        }

    # age_target (int 1-10, fallback to 7 — general adult reader)
    at = data.get("age_target")
    if isinstance(at, (int, float)):
        data["age_target"] = max(1, min(10, int(at)))
    elif isinstance(at, str) and at.strip().isdigit():
        data["age_target"] = max(1, min(10, int(at.strip())))
    else:
        data["age_target"] = 7

    # overall_summary
    if not isinstance(data.get("overall_summary"), str) or not data["overall_summary"].strip():
        data["overall_summary"] = "(Summary unavailable.)"
    else:
        data["overall_summary"] = data["overall_summary"].strip()[:2000]

    # ── Fiction-only narrative fields: still empty for non-fic ────────────
    # These are present in the HolisticAnalysis schema but irrelevant for non-fic.
    # Use enum .value references so a typo would fail at import time.
    data["pov_types"] = [POVType.THIRD_PERSON_LIMITED.value]  # arbitrary valid; downstream ignores
    data["pov_notes"] = ""
    data["sad_ending"] = False
    data["cliffhanger"] = False
    data["ending_notes"] = ""
    data["humor_types"] = [HumorType.NONE.value]

    # ── Character fields: relevant for memoir/biography/history/true_crime ──
    # Sanitize whatever the model returned (memoirs will fill these; cookbooks
    # / self-help / business return empty). Mirrors _sanitize_holistic_data.
    if not isinstance(data.get("character_arcs"), dict):
        data["character_arcs"] = {}
    if not isinstance(data.get("character_genders"), dict):
        data["character_genders"] = {}

    if not isinstance(data.get("character_archetypes"), dict):
        data["character_archetypes"] = {}
    else:
        sanitized_archetypes = {}
        for char_name, archs in data["character_archetypes"].items():
            if isinstance(archs, list):
                sanitized_archetypes[char_name] = _sanitize_enum_list(
                    archs, _VALID_ARCH, _ARCH_MAP, "other")[:3]
            elif isinstance(archs, str):
                sanitized_archetypes[char_name] = _sanitize_enum_list(
                    [archs], _VALID_ARCH, _ARCH_MAP, "other")[:3]
            else:
                sanitized_archetypes[char_name] = ["other"]
        data["character_archetypes"] = sanitized_archetypes

    if not isinstance(data.get("character_ages"), dict):
        data["character_ages"] = {}
    else:
        sanitized_ages = {}
        for char_name, age_val in data["character_ages"].items():
            if isinstance(char_name, str) and char_name.strip():
                sanitized_ages[char_name.strip()] = _sanitize_age(age_val)
        data["character_ages"] = sanitized_ages

    if isinstance(data.get("ranked_characters"), list):
        data["ranked_characters"] = [c.strip() for c in data["ranked_characters"]
                                      if isinstance(c, str) and c.strip()][:8]
    else:
        data["ranked_characters"] = []

    # Strip keys not in HolisticAnalysis schema so **data splat doesn't choke
    allowed = {
        "book_type", "genre", "sub_genres", "title", "author",
        "pov_types", "pov_notes", "setting", "reading_experience",
        "categories", "sad_ending", "cliffhanger", "ending_notes",
        "age_target", "content_flags", "overall_summary",
        "character_arcs", "character_genders", "character_archetypes",
        "character_ages", "ranked_themes", "ranked_characters", "humor_types",
    }
    return {k: v for k, v in data.items() if k in allowed}


def _build_fast_holistic_prompt(slim_analyses, extraction):
    """Rich Pass 2 prompt that handles themes, characters, and everything else."""
    # Build chunk spine from slim summaries
    chunk_summaries = []
    all_char_names = {}
    for sa in slim_analyses:
        chunk_summaries.append(
            f"[{sa.chunk_label}] Tone:{sa.tone} Violence:{sa.violence} "
            f"Humor:{sa.humor} Pace:{sa.pace} Romance:{sa.romance}. "
            f"Chars: {', '.join(sa.character_names[:5])}. "
            f"Summary: {sa.summary}")
        for name in sa.character_names:
            nl = name.lower().strip()
            if nl not in all_char_names:
                all_char_names[nl] = {"name": name, "count": 0}
            all_char_names[nl]["count"] += 1

    chunk_spine = "\n".join(chunk_summaries)

    # Get top characters by appearance frequency
    sorted_chars = sorted(all_char_names.values(), key=lambda x: x["count"], reverse=True)
    char_names = [c["name"] for c in sorted_chars[:15]]
    char_names_str = ", ".join(f'"{n}"' for n in char_names)

    genre_values = ", ".join(f'"{g.value}"' for g in Genre)
    book_type_values = ", ".join(f'"{b.value}"' for b in BookType)
    pov_values = ", ".join(f'"{p.value}"' for p in POVType)
    exp_values = ", ".join(f'"{e.value}"' for e in ReadingExperience)
    flag_values = ", ".join(f'"{f.value}"' for f in ContentFlag)
    humor_values = ", ".join(f'"{h.value}"' for h in HumorType)
    time_values = ", ".join(f'"{t.value}"' for t in TimePeriod)
    setting_values = ", ".join(f'"{s.value}"' for s in SettingType)
    arch_values = ", ".join(f'"{a.value}"' for a in CharacterArchetype)
    theme_list = "\n".join(f'  - "{t}"' for t in MASTER_THEMES)
    category_list = "\n".join(f'  - "{c}"' for c in MASTER_CATEGORIES)

    return f"""You are performing a COMPLETE analysis of a book.
You have the opening text, closing text, and section-by-section summaries with scores.
Respond ONLY with valid JSON. No preamble, no markdown.

BOOK: "{extraction.title_guess}" by {extraction.author_guess}
TOTAL: {extraction.total_words:,} words, {extraction.total_pages} pages

═══ AGE TARGET RUBRIC ═══
{_format_anchors("age_target")}

═══ OPENING TEXT ═══
{extraction.opening_text[:6000]}

═══ CLOSING TEXT ═══
{extraction.closing_text[:6000]}

═══ SECTION SPINE ═══
{chunk_spine}

═══ MASTER THEME LIST — pick top 10 from this list ONLY ═══
{theme_list}

═══ CATEGORY LIST — pick exactly 3 ═══
{category_list}

═══ REQUIRED JSON ═══
{{
  "book_type": one of [{book_type_values}],
  "genre": one of [{genre_values}],
  "sub_genres": ["1-4 sub-genre labels"],
  "title": "string",
  "author": "string",
  "pov_types": [{pov_values}],
  "pov_notes": "string",
  "setting": {{
    "primary_location": "string",
    "time_period": one of [{time_values}],
    "setting_type": one of [{setting_values}],
    "real_or_fictional": "real|fictional|mixed",
    "additional_locations": ["string"]
  }},
  "reading_experience": ["1-4 from: {exp_values}"],
  "categories": ["exactly 3 from category list"],
  "sad_ending": true|false,
  "cliffhanger": true|false,
  "ending_notes": "string",
  "age_target": int_1_to_10,
  "content_flags": ["pick the TOP 4 most significant from: {flag_values}"],
  "overall_summary": "3-5 sentence synthesis of the entire book",
  "character_arcs": {{
    "CharacterName": "1-3 sentence arc summary covering their FULL journey"
  }},
  "character_genders": {{
    "CharacterName": "male|female|non-binary|unknown"
  }},
  "character_archetypes": {{
    "CharacterName": ["1-3 archetypes from: {arch_values}"]
  }},
  "character_ages": {{
    "CharacterName": "child|teen|young_adult|adult|elderly|ageless|null"
  }},
  "ranked_themes": ["top 10 themes from MASTER LIST, ranked most important first"],
  "ranked_characters": ["top 8 character names, ranked most important first"],
  "humor_types": ["1-3 from: {humor_values}"]
}}

CRITICAL RULES:
- genre and book_type must be SINGLE string values.
- ranked_themes: Pick the TOP 10 themes from the MASTER THEME LIST that are truly
  CORE to this book. Use ONLY themes from the list. Rank most-important first.
- ranked_characters: From the detected characters [{char_names_str}], pick the TOP 8
  most important. Protagonist first. Do NOT include minor characters.
- You MUST provide character_arcs, character_genders, character_archetypes, and
  character_ages for each ranked character. Each arc must be 1-3 real sentences.
  Never empty or generic.
- sub_genres: Each sub-genre must be UNIQUE — no duplicates. 1-4 distinct sub-genres.
- content_flags: Pick only the TOP 4 most significant content warnings. Not themes.
- categories must be exactly 3 from the category list.
- Pay attention to CLOSING TEXT for sad_ending and cliffhanger.

JSON:"""


def aggregate_analysis_fast(slim_analyses, holistic, extraction):
    """Aggregate slim chunk data + rich holistic into final BookAnalysis."""
    def wavg(fn):
        tw = sum(c.word_count for c in slim_analyses) or 1
        return max(1, min(10, round(sum(fn(c) * c.word_count for c in slim_analyses) / tw)))

    metadata = BookMetadata(
        title=holistic.title, author=holistic.author,
        book_type=holistic.book_type, genre=holistic.genre,
        sub_genres=holistic.sub_genres,
        publisher=extraction.pdf_metadata.publisher,
        publish_year=extraction.pdf_metadata.publish_year,
        language=_resolve_language(extraction.pdf_metadata.language),
        isbn=extraction.pdf_metadata.isbn)

    ratings = ContentRatings(
        tone=wavg(lambda c: c.tone),
        readability=wavg(lambda c: c.readability),
        violence=wavg(lambda c: c.violence),
        age_target=holistic.age_target,
        pace=wavg(lambda c: c.pace),
        worldbuilding=wavg(lambda c: c.worldbuilding),
        humor=wavg(lambda c: c.humor),
        romance=wavg(lambda c: c.romance))

    sentences = [s.strip() for s in extraction.full_text.split(".") if s.strip()]
    asl = sum(len(s.split()) for s in sentences) / len(sentences) if sentences else 15.0
    wpp = extraction.total_words / max(extraction.total_pages, 1)
    wpm = max(100, min(400, 250 * (1.0 - (ratings.tone - 5) * 0.08)))

    computed = ComputedStats(
        total_words=extraction.total_words, total_pages=extraction.total_pages,
        avg_sentence_length=round(asl, 1), avg_words_per_page=round(wpp, 1),
        estimated_read_time_hours=round(extraction.total_words / wpm / 60, 2))

    # Themes from Pass 2 rankings (Pass 2 is sole source in fast mode)
    themes = []
    for i, tn in enumerate(holistic.ranked_themes[:10]):
        prom = max(4, 10 - i)  # 1st=10, 2nd=9, etc.
        themes.append(Theme(name=tn.lower().strip(), prominence=prom))

    # Characters from Pass 2 rankings
    arcs = holistic.character_arcs or {}
    genders = holistic.character_genders or {}
    archetypes_map = holistic.character_archetypes or {}
    ages_map = holistic.character_ages or {}
    characters = []
    for char_name in holistic.ranked_characters[:8]:
        arc = ""
        for ak, av in arcs.items():
            if ak.lower().strip() == char_name.lower().strip() or \
               _same_char(ak.lower(), char_name.lower()):
                arc = av
                break
        if not arc or len(arc.strip()) < 10:
            continue

        gender = "unknown"
        for gk, gv in genders.items():
            if gk.lower().strip() == char_name.lower().strip() or \
               _same_char(gk.lower(), char_name.lower()):
                gender = gv.lower().strip()
                break
        if gender not in ("male", "female", "non-binary", "unknown"):
            gender = "unknown"

        # Archetypes from Pass 2 (fallback to OTHER)
        char_archetypes = [CharacterArchetype.OTHER]
        for ark, arv in archetypes_map.items():
            if ark.lower().strip() == char_name.lower().strip() or \
               _same_char(ark.lower(), char_name.lower()):
                if isinstance(arv, list) and arv:
                    mapped = []
                    for a in arv:
                        if isinstance(a, str):
                            al = a.lower().strip().replace(" ", "_").replace("-", "_")
                            if al in _VALID_ARCH:
                                mapped.append(CharacterArchetype(al))
                            elif al in _ARCH_MAP and _ARCH_MAP[al] != "_skip":
                                mapped.append(CharacterArchetype(_ARCH_MAP[al]))
                    char_archetypes = mapped[:3] if mapped else [CharacterArchetype.OTHER]
                break

        # Age category from Pass 2 (fallback to None)
        age_cat = None
        valid_ages = {"child", "teen", "young_adult", "adult", "elderly", "ageless"}
        for agk, agv in ages_map.items():
            if agk.lower().strip() == char_name.lower().strip() or \
               _same_char(agk.lower(), char_name.lower()):
                if isinstance(agv, str) and agv.lower().strip() in valid_ages:
                    age_cat = agv.lower().strip()
                break

        # Count appearances across slim chunks for importance
        appear_count = 0
        for sa in slim_analyses:
            for cn in sa.character_names:
                if _same_char(cn.lower(), char_name.lower()):
                    appear_count += 1
                    break

        # Importance based on appearance ratio
        ratio = appear_count / len(slim_analyses) if slim_analyses else 0
        if ratio > 0.5:
            imp = 10
        elif ratio > 0.3:
            imp = 9
        elif ratio > 0.2:
            imp = 8
        elif ratio > 0.1:
            imp = 7
        elif ratio > 0.05:
            imp = 6
        else:
            imp = 5

        characters.append(Character(
            name=char_name, importance=imp,
            gender=gender, archetypes=char_archetypes,
            arc_summary=arc, age_category=age_cat))

    humor = HumorProfile(
        humor_density=ratings.humor,
        primary_humor_types=holistic.humor_types if holistic.humor_types else [HumorType.NONE])

    # Content flags: frequency-based, dynamically capped at 0-6
    chunk_flag_lists = [list(sa.content_flags) for sa in slim_analyses]
    cflags = _aggregate_content_flags(
        chunk_flag_lists, list(holistic.content_flags), len(slim_analyses))

    return BookAnalysis(
        metadata=metadata, themes=themes,
        categories=holistic.categories,
        characters=characters,
        setting=holistic.setting, ratings=ratings, computed_stats=computed,
        humor=humor, pov=holistic.pov_types, pov_notes=holistic.pov_notes,
        reading_experience=holistic.reading_experience,
        sad_ending=holistic.sad_ending, cliffhanger=holistic.cliffhanger,
        ending_notes=holistic.ending_notes, content_flags=cflags,
        overall_summary=holistic.overall_summary)


def aggregate_analysis_nonfic(pass1_analyses, holistic, nfi, extraction):
    """
    Non-fiction aggregator (Session D). Parallel to aggregate_analysis_fast,
    but with fiction-specific fields populated from their schema defaults
    (characters=[], humor=default profile, sad_ending=False, etc.) instead of
    from holistic. The non_fiction_info block is populated from `nfi`.

    Args:
        pass1_analyses: list of ChunkAnalysis OR SlimChunkAnalysis (same as
            the fiction aggregators — works either way because we only read
            common fields: word_count, tone, readability, pace, age_target,
            content_flags).
        holistic: HolisticAnalysis produced by analyze_holistic_nonfic_full.
            Has universal fields populated (genre, themes, categories, setting,
            summary, etc.). Fiction-only fields in this object are minimal
            defaults from the universal sanitizer — they get discarded here.
        nfi: NonFictionInfo produced by analyze_holistic_nonfic_full.
        extraction: ExtractionResult for metadata + computed stats.

    Returns:
        Complete BookAnalysis with non_fiction_info populated.

    Design notes:
        - Ratings violence/worldbuilding/humor/romance default to 1 (Optional
          in schema). Pass 1 still produces per-chunk values for these but we
          deliberately don't surface them for non-fiction — content_flags
          handles content-warning use cases better than single integers.
        - Themes come from holistic.ranked_themes (which the unified non-fic
          Pass 2 populated). Same source as the fiction path.
        - Characters list is empty. Pass 1's character_names for non-fiction
          would mostly be the author or real people mentioned; capturing them
          here would require addendum-specific logic (Session E for memoirs/
          biographies/true_crime). For now: empty list.
        - Setting comes from holistic.setting. For memoirs/history/travel/true
          crime, the model populated real setting info. For self-help/etc.,
          it's the minimal default.
    """
    def wavg(fn):
        tw = sum(c.word_count for c in pass1_analyses) or 1
        return max(1, min(10, round(sum(fn(c) * c.word_count for c in pass1_analyses) / tw)))

    metadata = BookMetadata(
        title=holistic.title, author=holistic.author,
        book_type=holistic.book_type, genre=holistic.genre,
        sub_genres=holistic.sub_genres,
        publisher=extraction.pdf_metadata.publisher,
        publish_year=extraction.pdf_metadata.publish_year,
        language=_resolve_language(extraction.pdf_metadata.language),
        isbn=extraction.pdf_metadata.isbn)

    # Universal ratings: averaged from Pass 1 chunks. Fiction-specific ratings
    # (violence/worldbuilding/humor/romance) default to 1 — they're Optional in
    # the schema and content_flags handles content warnings better.
    ratings = ContentRatings(
        tone=wavg(lambda c: c.tone),
        readability=wavg(lambda c: c.readability),
        pace=wavg(lambda c: c.pace),
        age_target=holistic.age_target,
        # Fiction-specific ratings stay at their schema defaults (=1)
    )

    sentences = [s.strip() for s in extraction.full_text.split(".") if s.strip()]
    asl = sum(len(s.split()) for s in sentences) / len(sentences) if sentences else 15.0
    wpp = extraction.total_words / max(extraction.total_pages, 1)
    wpm = max(100, min(400, 250 * (1.0 - (ratings.tone - 5) * 0.08)))

    computed = ComputedStats(
        total_words=extraction.total_words, total_pages=extraction.total_pages,
        avg_sentence_length=round(asl, 1), avg_words_per_page=round(wpp, 1),
        estimated_read_time_hours=round(extraction.total_words / wpm / 60, 2))

    # Themes from Pass 2 rankings (unified non-fic Pass 2 produced these)
    themes = []
    for i, tn in enumerate(holistic.ranked_themes[:10]):
        prom = max(4, 10 - i)
        themes.append(Theme(name=tn.lower().strip(), prominence=prom))

    # Content flags: same aggregation as fiction path. Non-fiction books still
    # have meaningful content flags (war, abuse, death, trauma — these matter).
    chunk_flag_lists = [list(getattr(sa, "content_flags", [])) for sa in pass1_analyses]
    cflags = _aggregate_content_flags(
        chunk_flag_lists, list(holistic.content_flags), len(pass1_analyses))

    # Construct BookAnalysis. Fiction-specific fields explicitly use their
    # schema defaults (characters=[], humor=default profile, pov=[], pov_notes="",
    # sad_ending=False, cliffhanger=False, ending_notes="") via *omission* —
    # the BookAnalysis class has default_factory for all of these (set up in A4).
    return BookAnalysis(
        metadata=metadata,
        themes=themes,
        categories=holistic.categories,
        setting=holistic.setting,
        ratings=ratings,
        computed_stats=computed,
        reading_experience=holistic.reading_experience,
        content_flags=cflags,
        overall_summary=holistic.overall_summary,
        non_fiction_info=nfi,
        # Fiction-specific fields omitted — they take their default_factory
        # values from the BookAnalysis schema (empty list, default humor profile,
        # False for sad_ending/cliffhanger, etc.)
    )

# ═══════════════════════════════════════════════════════════════════════════════
# NON-FICTION STUB (Session B)
# ═══════════════════════════════════════════════════════════════════════════════
# These placeholders let us wire detection + routing in Session B without
# having the actual non-fiction Pass 2 prompt yet. Session C replaces the
# stub-generating function with a real Pass 2 that populates NonFictionInfo
# from actual book content.

def make_stub_nonfic_info() -> NonFictionInfo:
    """
    Build a placeholder NonFictionInfo. Marked clearly so it's obvious in
    output JSON that this is a stub, not real analysis.

    Used by Session B routing for non-fiction books to verify the pipeline
    can produce a valid BookAnalysis with non_fiction_info populated.
    Session C replaces this with a real Pass 2 prompt + sanitizer.
    """
    return NonFictionInfo(
        thesis="[STUB - Session B placeholder, real thesis arrives in Session C]",
        target_audience=TargetAudience.GENERAL_READER,
        prerequisites=[],
        structure_type=StructureType.MIXED,
        tone_register=[ToneRegister.CONVERSATIONAL],
        practical_vs_theoretical=5,
        conclusion_type=ConclusionType.OPEN_ENDED,
        sub_type=BookSubType.OTHER,
        # All 11 addendum slots remain None — populated in Session E
    )