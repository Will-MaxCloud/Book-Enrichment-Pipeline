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
    CharacterArchetype, BookType, Genre, TimePeriod, SettingType,
)
from extractor import TextChunk, ExtractionResult

logger = logging.getLogger(__name__)


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
      "role": "string",
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
- Content flags are warnings only — not themes.

TEXT:
---
{chunk.text[:14000]}
---

JSON:"""


# ── Cached prompt builders (split static system / dynamic user) ───────────

def _build_chunk_prompt_cached(chunk, book_context):
    """Returns (system_prompt, user_prompt) for prompt caching."""
    theme_list = "\n".join(f'  - "{t}"' for t in MASTER_THEMES)
    archetype_values = ", ".join(f'"{a.value}"' for a in CharacterArchetype)
    humor_values = ", ".join(f'"{h.value}"' for h in HumorType)
    flag_values = ", ".join(f'"{f.value}"' for f in ContentFlag)

    system = f"""You are a literary analyst. Respond ONLY with valid JSON. No preamble, no markdown.

SCORING RUBRICS:
TONE (1-10): {_format_anchors("tone")}
READABILITY (1-10): {_format_anchors("readability")}
VIOLENCE (1-10): {_format_anchors("violence")}
PACE (1-10): {_format_anchors("pace")}
WORLDBUILDING (1-10): {_format_anchors("worldbuilding")}
HUMOR (1-10): {_format_anchors("humor")}
ROMANCE (1-10): {_format_anchors("romance")}
CHARACTER IMPORTANCE (1-10): {_format_anchors("character_importance")}
THEME PROMINENCE (1-10): {_format_anchors("prominence")}

MASTER THEME LIST (pick from this list ONLY):
{theme_list}

CONTENT FLAGS (content warnings ONLY): {flag_values}
"sexual_content" = consensual. "sexual_violence" = assault.

Return JSON with: chunk_index, chunk_label, word_count, themes_detected, theme_prominences,
characters_present (name, role, importance, gender, archetypes [{archetype_values}], arc_summary, age_category),
humor_density, humor_types [{humor_values}], tone, readability_score, violence_level, pace_score,
romance_level, worldbuilding_level, content_flags, notable_observations.

RULES: 2-8 themes from MASTER LIST ONLY. Only named characters with real arc_summaries. Content flags are warnings only."""

    user = f"""CONTEXT: {book_context}
SECTION: {chunk.label} (pages {chunk.page_start}-{chunk.page_end}, ~{chunk.word_count} words)

Analyze and return JSON:
---
{chunk.text[:14000]}
---
JSON:"""
    return system, user


def _build_slim_chunk_prompt_cached(chunk, book_context):
    """Returns (system_prompt, user_prompt) for cached slim analysis."""
    flag_values = ", ".join(f'"{f.value}"' for f in ContentFlag)

    system = f"""Score text sections. Respond ONLY with JSON, no preamble.
SCORING (1-10): tone (1=light, 10=dark), readability (1=hard, 10=easy),
violence (1=none, 10=extreme), pace (1=slow, 10=fast), worldbuilding (1=none, 10=exhaustive),
humor (1=none, 10=maximum), romance (1=none, 10=maximum).
Return JSON with: chunk_index, chunk_label, word_count, tone, readability, violence,
pace, worldbuilding, humor, romance (all int 1-10), character_names (list),
content_flags [{flag_values}], summary (one sentence)."""

    user = f"""CONTEXT: {book_context}
SECTION: {chunk.label} (~{chunk.word_count} words)

---
{chunk.text[:14000]}
---
JSON:"""
    return system, user


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
- Pay attention to CLOSING TEXT for sad_ending and cliffhanger.

JSON:"""


# ═══════════════════════════════════════════════════════════════════════════════
# API CLIENT
# ═══════════════════════════════════════════════════════════════════════════════

class AnalysisClient:
    def __init__(self, api_key, model="claude-sonnet-4-20250514",
                 p2_model=None, max_retries=3, retry_delay=2.0,
                 max_concurrent=2):
        self.api_key = api_key
        self.model = model
        self.p2_model = p2_model or model
        self.max_retries, self.retry_delay = max_retries, retry_delay
        self.max_concurrent = max_concurrent

    # ── Core API calls (sync + async) ─────────────────────────────────────

    def _call_api(self, prompt, max_tokens=4096, model_override=None,
                  system_prompt=None):
        """Sync API call. If system_prompt provided, enables prompt caching."""
        import anthropic
        client = anthropic.Anthropic(api_key=self.api_key)
        use_model = model_override or self.model

        # Build request kwargs
        kwargs = {
            "model": use_model,
            "max_tokens": max_tokens,
            "temperature": 0.0,
            "messages": [{"role": "user", "content": prompt}],
        }

        # Add cached system prompt if provided
        if system_prompt:
            kwargs["system"] = [{
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }]

        for attempt in range(1, self.max_retries + 1):
            try:
                r = client.messages.create(**kwargs)
                return "".join(b.text for b in r.content if b.type == "text").strip()
            except anthropic.RateLimitError:
                w = self.retry_delay * (2 ** (attempt - 1))
                logger.warning(f"Rate limited — {w:.1f}s (attempt {attempt})")
                time.sleep(w)
            except anthropic.APIError as e:
                logger.error(f"API error attempt {attempt}: {e}")
                if attempt == self.max_retries: raise
                time.sleep(self.retry_delay)
        raise RuntimeError(f"Failed after {self.max_retries} retries")

    async def _call_api_async(self, prompt, max_tokens=4096, model_override=None,
                               system_prompt=None):
        """Async API call with prompt caching support."""
        import anthropic
        import asyncio
        client = anthropic.AsyncAnthropic(api_key=self.api_key)
        use_model = model_override or self.model

        kwargs = {
            "model": use_model,
            "max_tokens": max_tokens,
            "temperature": 0.0,
            "messages": [{"role": "user", "content": prompt}],
        }

        if system_prompt:
            kwargs["system"] = [{
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }]

        for attempt in range(1, self.max_retries + 1):
            try:
                r = await client.messages.create(**kwargs)
                return "".join(b.text for b in r.content if b.type == "text").strip()
            except anthropic.RateLimitError:
                w = self.retry_delay * (2 ** (attempt - 1))
                logger.warning(f"Rate limited — {w:.1f}s (attempt {attempt})")
                await asyncio.sleep(w)
            except anthropic.APIError as e:
                logger.error(f"API error attempt {attempt}: {e}")
                if attempt == self.max_retries: raise
                await asyncio.sleep(self.retry_delay)
        raise RuntimeError(f"Failed after {self.max_retries} retries")

    # ── Full mode (sync) ──────────────────────────────────────────────────

    def analyze_chunk(self, chunk, book_context):
        system, user = _build_chunk_prompt_cached(chunk, book_context)
        return ChunkAnalysis(**_sanitize_chunk_data(
            _safe_parse_json(self._call_api(user, system_prompt=system))))

    def analyze_holistic(self, chunk_analyses, extraction):
        return HolisticAnalysis(**_sanitize_holistic_data(
            _safe_parse_json(self._call_api(
                _build_holistic_prompt(chunk_analyses, extraction), 4096,
                model_override=self.p2_model))))

    # ── Fast mode (sync) ──────────────────────────────────────────────────

    def analyze_chunk_slim(self, chunk, book_context):
        system, user = _build_slim_chunk_prompt_cached(chunk, book_context)
        return SlimChunkAnalysis(**_sanitize_slim_chunk_data(
            _safe_parse_json(self._call_api(user, 512, system_prompt=system))))

    def analyze_holistic_fast(self, slim_analyses, extraction):
        return HolisticAnalysis(**_sanitize_holistic_data(
            _safe_parse_json(self._call_api(
                _build_fast_holistic_prompt(slim_analyses, extraction), 4096,
                model_override=self.p2_model))))

    # ── Parallel chunk processing ─────────────────────────────────────────

    async def analyze_chunks_parallel(self, chunks, book_context, slim=False):
        """
        Process chunks concurrently with safety controls:
        - Max 2 concurrent requests (semaphore)
        - 1s stagger delay between launching each task
        - Full retry logic per request
        """
        import asyncio
        semaphore = asyncio.Semaphore(self.max_concurrent)
        results = [None] * len(chunks)
        failed = []

        async def process_chunk(i, chunk):
            async with semaphore:
                try:
                    if slim:
                        system, user = _build_slim_chunk_prompt_cached(chunk, book_context)
                        raw = await self._call_api_async(user, 512, system_prompt=system)
                        results[i] = SlimChunkAnalysis(**_sanitize_slim_chunk_data(
                            _safe_parse_json(raw)))
                    else:
                        system, user = _build_chunk_prompt_cached(chunk, book_context)
                        raw = await self._call_api_async(user, system_prompt=system)
                        results[i] = ChunkAnalysis(**_sanitize_chunk_data(
                            _safe_parse_json(raw)))
                    logger.info(f"  ✓ Chunk {i+1}/{len(chunks)} complete")
                except Exception as e:
                    logger.error(f"  ✗ Chunk {i} failed: {e}")
                    failed.append(i)

        # Stagger launches — 1s between each to prevent rate limit bursts
        tasks = []
        for i, chunk in enumerate(chunks):
            task = asyncio.create_task(process_chunk(i, chunk))
            tasks.append(task)
            await asyncio.sleep(1.0)

        await asyncio.gather(*tasks)

        successful = [r for r in results if r is not None]
        return successful, failed


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
    "intimacy": "sexual_content", "explicit_sex": "sexual_content",
    "drugs": "substance_abuse", "alcohol": "substance_abuse",
    "drinking": "substance_abuse", "drug_use": "substance_abuse",
    "alcoholism": "substance_abuse", "overdose": "substance_abuse",
    "smoking": "substance_abuse", "addiction": "substance_abuse",
    "withdrawal": "substance_abuse",
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
    "neglect": "abuse", "manipulation": "abuse",
    "gaslighting": "abuse", "cruelty": "abuse",
    "stalking": "abuse", "harassment": "abuse",
    "verbal_abuse": "abuse", "toxic_relationship": "abuse",
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

    if "content_flags" in data:
        data["content_flags"] = _sanitize_enum_list(data["content_flags"], _VALID_FLAGS, _FLAG_MAP, "none")
    if "humor_types" in data:
        data["humor_types"] = _sanitize_enum_list(data["humor_types"], _VALID_HUMOR, None, "none")

    for f in ["humor_density", "tone", "readability_score", "violence_level",
              "pace_score", "romance_level", "worldbuilding_level"]:
        if f in data and isinstance(data[f], (int, float)):
            data[f] = max(1, min(10, int(data[f])))

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

    # Sub-genres (with deduplication)
    if "sub_genres" in data and isinstance(data["sub_genres"], list):
        norm = [_normalize_sub_genre(sg) for sg in data["sub_genres"] if isinstance(sg, str)]
        seen_sg = set()
        deduped_sg = []
        for s in norm:
            if s and s.lower() not in seen_sg:
                seen_sg.add(s.lower())
                deduped_sg.append(s)
        data["sub_genres"] = deduped_sg[:4] or ["literary fiction"]
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
        # Sanitize each character's archetypes list
        cleaned_archs = {}
        for name, archs in data["character_archetypes"].items():
            if isinstance(archs, list):
                sanitized = _sanitize_enum_list(archs, _VALID_ARCH, _ARCH_MAP, "other")[:3]
                cleaned_archs[name] = sanitized
            elif isinstance(archs, str):
                sanitized = _sanitize_enum_list([archs], _VALID_ARCH, _ARCH_MAP, "other")[:3]
                cleaned_archs[name] = sanitized
        data["character_archetypes"] = cleaned_archs
    if "character_ages" not in data or not isinstance(data.get("character_ages"), dict):
        data["character_ages"] = {}
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
        publish_year=extraction.pdf_metadata.publish_year)

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

    flags = set()
    for c in chunk_analyses: flags.update(c.content_flags)
    flags.update(holistic.content_flags)
    flags.discard(ContentFlag.NONE)
    cflags = sorted(flags, key=lambda f: f.value) or [ContentFlag.NONE]

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
                raw[k] = {"name": ch.name, "role": ch.role,
                          "imp": [], "arch": [], "age": None,
                          "gender": "unknown", "cnt": 0}
            d = raw[k]
            d["imp"].append(ch.importance); d["arch"].extend(ch.archetypes)
            d["cnt"] += 1
            if ch.age_category: d["age"] = ch.age_category
            if hasattr(ch, "gender") and ch.gender and ch.gender != "unknown":
                d["gender"] = ch.gender
            if len(ch.role) > len(d["role"]): d["role"] = ch.role

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
                name=cd["name"], role=cd["role"],
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
            name=cd["name"], role=cd["role"], importance=imp,
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
            result[p] = {"name": pd["name"], "all": {p}, "role": pd["role"],
                         "imp": list(pd["imp"]), "arch": list(pd["arch"]),
                         "age": pd["age"], "cnt": pd["cnt"]}
        if k != p:
            r = result[p]; r["all"].add(k)
            r["imp"].extend(cd["imp"]); r["arch"].extend(cd["arch"])
            r["cnt"] += cd["cnt"]
            if cd["age"] and not r["age"]: r["age"] = cd["age"]
            if len(cd["role"]) > len(r["role"]): r["role"] = cd["role"]
            if len(cd["name"]) > len(r["name"]): r["name"] = cd["name"]
    return result

def _same_char(a, b):
    a, b = a.lower().strip(), b.lower().strip()
    if a == b: return True
    if len(a) < 3 or len(b) < 3: return False
    sh, lo = (a, b) if len(a) <= len(b) else (b, a)
    if sh in lo and len(sh) >= 3: return True
    ml = min(len(a), len(b))
    sp = 0
    for i in range(ml):
        if a[i] == b[i]: sp += 1
        else: break
    if sp >= 3: return True
    ss = 0
    for i in range(1, ml+1):
        if a[-i] == b[-i]: ss += 1
        else: break
    return ss >= 3

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
                cd[k] = {"name": ch.name, "role": ch.role, "max_importance": 0, "chunk_count": 0}
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
            rd[p] = {"name": pd["name"], "role": pd["role"],
                     "max_importance": pd["max_importance"], "chunk_count": pd["chunk_count"]}
        if k != p:
            r = rd[p]
            r["max_importance"] = max(r["max_importance"], d["max_importance"])
            r["chunk_count"] += d["chunk_count"]
            if len(d["name"]) > len(r["name"]): r["name"] = d["name"]
    return sorted(rd.values(), key=lambda x: x["max_importance"], reverse=True)

# ═══════════════════════════════════════════════════════════════════════════════
# FAST MODE — Slim Pass 1 (Haiku) + Rich Pass 2 (Sonnet)
# ═══════════════════════════════════════════════════════════════════════════════

def _build_slim_chunk_prompt(chunk, book_context):
    """Tiny prompt for Haiku — just scores, names, flags, summary."""
    flag_values = ", ".join(f'"{f.value}"' for f in ContentFlag)

    return f"""Score this text section. Respond ONLY with JSON, no preamble.

CONTEXT: {book_context}
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
  "character_names": ["list every named character who appears"],
  "content_flags": [{flag_values}],
  "summary": "One sentence summary of what happens in this section."
}}

TEXT:
---
{chunk.text[:14000]}
---

JSON:"""


def _sanitize_slim_chunk_data(data):
    """Sanitize slim chunk data."""
    for f in ["tone", "readability", "violence", "pace",
              "worldbuilding", "humor", "romance"]:
        if f in data and isinstance(data[f], (int, float)):
            data[f] = max(1, min(10, int(data[f])))
        elif f not in data:
            data[f] = 5

    if "character_names" in data and isinstance(data["character_names"], list):
        data["character_names"] = [n.strip() for n in data["character_names"]
                                    if isinstance(n, str) and n.strip() and len(n.strip()) >= 2]
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
    "CharacterName": "child|teen|young_adult|adult|elderly"
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
- You MUST provide character_arcs, character_genders, character_archetypes, and character_ages for each ranked character.
  Each arc must be 1-3 real sentences. Never empty or generic.
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
        publish_year=extraction.pdf_metadata.publish_year)

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

        # Archetypes from Pass 2
        char_archetypes = [CharacterArchetype.OTHER]
        for aak, aav in archetypes_map.items():
            if aak.lower().strip() == char_name.lower().strip() or \
               _same_char(aak.lower(), char_name.lower()):
                if isinstance(aav, list) and aav:
                    # Already sanitized by _sanitize_holistic_data
                    char_archetypes = []
                    for arch_str in aav[:3]:
                        try:
                            char_archetypes.append(CharacterArchetype(arch_str))
                        except ValueError:
                            pass
                    if not char_archetypes:
                        char_archetypes = [CharacterArchetype.OTHER]
                break

        # Age from Pass 2
        char_age = None
        for agk, agv in ages_map.items():
            if agk.lower().strip() == char_name.lower().strip() or \
               _same_char(agk.lower(), char_name.lower()):
                if isinstance(agv, str) and agv.lower().strip() in (
                    "child", "teen", "young_adult", "adult", "elderly"):
                    char_age = agv.lower().strip()
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
            name=char_name, role="", importance=imp,
            gender=gender, archetypes=char_archetypes,
            arc_summary=arc, age_category=char_age))

    humor = HumorProfile(
        humor_density=ratings.humor,
        primary_humor_types=holistic.humor_types if holistic.humor_types else [HumorType.NONE])

    # Content flags: union from chunks, but capped at 4 by Pass 2
    flags = set()
    for sa in slim_analyses:
        flags.update(sa.content_flags)
    flags.update(holistic.content_flags)
    flags.discard(ContentFlag.NONE)
    # Use Pass 2's flags as the curated top 4
    p2_flags = set(holistic.content_flags)
    p2_flags.discard(ContentFlag.NONE)
    cflags = sorted(p2_flags, key=lambda f: f.value)[:4] or [ContentFlag.NONE]

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