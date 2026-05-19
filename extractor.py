"""
Book text extraction, chapter segmentation, and metadata extraction.

Supports: PDF (.pdf) and EPUB (.epub)

PDF: Uses pymupdf (fitz) — fast, lightweight, handles most book PDFs.
EPUB: Uses ebooklib + beautifulsoup4 — extracts from HTML chapters.

Both formats produce identical ExtractionResult output, so the rest
of the pipeline doesn't care which format the book was in.

CHUNKING STRATEGY:
1. Extract all text (page-by-page for PDF, chapter-by-chapter for EPUB)
2. Attempt chapter-based segmentation (preserves narrative units)
3. Fall back to windowed chunks with overlap if no chapters detected
4. Always preserve FIRST and LAST text for Pass 2 context
5. Cap chunk size at ~5000 words for API token safety

Install for EPUB support:
    pip install ebooklib beautifulsoup4
"""

from __future__ import annotations
import os
import re
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class TextChunk:
    """A segment of extracted text with metadata."""
    index: int
    label: str
    text: str
    page_start: int
    page_end: int
    word_count: int = 0

    def __post_init__(self):
        self.word_count = len(self.text.split())


@dataclass
class PDFMetadata:
    """Metadata extracted from PDF/EPUB document properties."""
    pdf_title: Optional[str] = None
    pdf_author: Optional[str] = None
    publisher: Optional[str] = None
    publish_year: Optional[int] = None
    creator: Optional[str] = None
    subject: Optional[str] = None
    language: Optional[str] = None
    isbn: Optional[str] = None


@dataclass
class ExtractionResult:
    """Complete extraction output from a PDF."""
    full_text: str
    chunks: list[TextChunk]
    total_pages: int
    total_words: int
    title_guess: str
    author_guess: str
    pdf_metadata: PDFMetadata
    # Special context for Pass 2 (holistic analysis)
    opening_text: str  # First ~1500 words
    closing_text: str  # Last ~1500 words
    extraction_warnings: list[str] = field(default_factory=list)


# ── Chapter detection patterns ────────────────────────────────────────────────

CHAPTER_PATTERNS = [
    re.compile(
        r"^\s*(CHAPTER|Chapter)\s+(\d+|[IVXLCDM]+|"
        r"[Oo]ne|[Tt]wo|[Tt]hree|[Ff]our|[Ff]ive|[Ss]ix|[Ss]even|"
        r"[Ee]ight|[Nn]ine|[Tt]en|[Ee]leven|[Tt]welve)",
        re.MULTILINE,
    ),
    re.compile(r"^\s*(PART|Part)\s+(\d+|[IVXLCDM]+|[A-Z][a-z]+)", re.MULTILINE),
    re.compile(r"^\s*(\d+|[IVXLCDM]+)\.\s+[A-Z]", re.MULTILINE),
]


# ── Back-matter detection (trim-from-tail approach) ──────────────────────────
#
# DESIGN PRINCIPLES:
# 1. Only trim from the END of the book, scanning backwards
# 2. Stop the moment we hit a section that looks like story content
# 3. Never filter sections in the middle or beginning of the book
# 4. Err on the side of INCLUDING content (lenient, not strict)
# 5. Multilingual — covers major European languages
#
# This means: a chapter called "The Acknowledgment" at position 50% is
# always safe, because we'd hit story chapters after it and stop.

# Patterns that DEFINITIVELY mark a section as story content.
# If we see any of these while scanning backwards, we STOP trimming immediately.
# Multilingual: English, Italian, French, Spanish, Portuguese, German,
#               Dutch, Swedish, Norwegian, Danish, Polish, Russian (transliterated)
_STORY_MARKERS = re.compile(
    r"(?i)^\s*("
    # Chapter markers (match full chapter number/word with \w+)
    r"chapter\s+\w+|capitolo\s+\w+|chapitre\s+\w+|cap[ií]tulo\s+\w+"
    r"|kapitel\s+\w+|hoofdstuk\s+\w+|kapittel\s+\w+|kapitola\s+\w+"
    r"|rozdzia[lł]\s+\w+|глава\s+\w+"
    r"|cap\.\s*\w+|ch\.\s*\d+|chap\.\s*\d+"
    # Part / book / volume markers
    r"|part\s+\w+|parte\s+\w+|partie\s+\w+|teil\s+\w+|deel\s+\w+|del\s+\w+"
    r"|book\s+(?:one|two|three|four|i|ii|iii|iv|v|\d+)"
    r"|volume\s+(?:one|two|i|ii|iii|\d+)|libro\s+\w+|livre\s+\w+|buch\s+\w+"
    # Numbered sections (1. / I. / 01. followed by title word)
    r"|\d+\.\s+[A-Z]\w*|[IVXLCDM]+\.\s+[A-Z]\w*"
    # Prologue / Epilogue / Interlude (these ARE story content)
    r"|prologue|prologo|pr[oó]logo|prolog|proloog|forspill"
    r"|epilogue|epilogo|ep[ií]logo|epilog|epiloog|etterord"
    r"|interlude|interludio|zwischenspiel"
    # "The End" markers
    r"|the\s+end\s*$|fin\s*$|fine\s*$|ende\s*$|fim\s*$|конец\s*$"
    r")\b",
    re.MULTILINE,
)

# High-confidence back-matter headings (multilingual).
# These are terms that essentially NEVER appear as chapter titles in narrative.
# Languages: EN, IT, FR, ES, PT, DE, NL, SV, NO, DA, PL
_BACK_MATTER_HEADINGS = re.compile(
    r"(?i)^\s*("

    # ── Acknowledgments / Thanks ───────────────────────────────────────────
    r"acknowledg[e]?ments?|ringraziamenti|remerciements|agradecimientos"
    r"|agradecimentos|danksagung|dankwoord|tacks[äa]gelse|takksigelser"
    r"|podzi[eę]kowania"

    # ── Copyright / Legal / Imprint ────────────────────────────────────────
    r"|copyright|all\s+rights\s+reserved"
    r"|tutti\s+i\s+diritti\s+riservati|nota\s+di\s+copyright"
    r"|tous\s+droits\s+r[eé]serv[eé]s|mentions\s+l[eé]gales"
    r"|todos\s+los\s+derechos\s+reservados|aviso\s+legal"
    r"|todos\s+os\s+direitos\s+reservados"
    r"|alle\s+rechte\s+vorbehalten|impressum|urheberrecht"
    r"|alle\s+rechten\s+voorbehouden|colofoon"
    r"|wszelkie\s+prawa\s+zastrze[zż]one"

    # ── About the author / Author bio ──────────────────────────────────────
    r"|about\s+the\s+author[s]?|author\s+bio(?:graphy)?"
    r"|sull['\u2019]?\s*autor[ei]|nota\s+sull['\u2019]?\s*autor[ei]"
    r"|biografia\s+dell['\u2019]?\s*autor[ei]|gli\s+autori|l['\u2019]?\s*autore"
    r"|[àa]\s+propos\s+de\s+l['\u2019]?\s*auteur|biographie\s+de\s+l['\u2019]?\s*auteur"
    r"|les\s+auteurs"
    r"|sobre\s+el\s+autor|acerca\s+del\s+autor|biograf[ií]a\s+del\s+autor"
    r"|los\s+autores"
    r"|sobre\s+o\s+autor|biografia\s+do\s+autor"
    r"|[üu]ber\s+d(?:en|ie)\s+autor(?:in)?|biografie\s+des\s+autors"
    r"|over\s+de\s+auteur|om\s+f[öo]rfattaren"

    # ── Also by / Other works ──────────────────────────────────────────────
    r"|also\s+by|other\s+(?:books|works|titles)\s+by|by\s+the\s+same\s+author"
    r"|dello\s+stesso\s+autor[ei]|altre\s+opere|nello\s+stesso\s+catalogo"
    r"|du\s+m[êe]me\s+auteur|du\s+m[êe]me\s+[eé]diteur"
    r"|del\s+mismo\s+autor|otros\s+t[ií]tulos|otras\s+obras"
    r"|do\s+mesmo\s+autor"
    r"|vom\s+selben\s+autor|weitere\s+b[üu]cher"
    r"|van\s+dezelfde\s+auteur"

    # ── Author/editor notes (positioned at end = back matter) ──────────────
    r"|author['\u2019]?s?\s+note|editor['\u2019]?s?\s+note|translator['\u2019]?s?\s+note"
    r"|nota\s+(?:dell['\u2019]?\s*autor|dell['\u2019]?\s*editor|del\s+traduttor)[ei]"
    r"|note\s+de\s+l['\u2019]?\s*(?:auteur|[eé]diteur|traducteur)"
    r"|nota\s+del\s+(?:autor|editor|traductor)"
    r"|anmerkung\s+des\s+(?:autors|herausgebers|[üu]bersetzers)"

    # ── Interviews ─────────────────────────────────────────────────────────
    r"|interview\s+with|intervista\s+(?:con|a)|entretien\s+avec"
    r"|entrevista\s+(?:con|a)|entrevista\s+com"
    r"|gespr[äa]ch\s+mit|interview\s+met"
    r"|q\s*[&y]\s*a\s+with|q\s+and\s+a"

    # ── Forewords / Introductions / Prefaces (back-matter when at end) ─────
    # Note: only flagged when at END of book — front-matter scan untouched
    r"|afterword|postface|postfazione|epil[oó]go\s+del\s+autor|nachwort"
    r"|nawoord|etterord|efterord|posłowie"

    # ── Colophon / Credits / Newsletter / Bonus ────────────────────────────
    r"|colophon|colofon|colof[oó]n|kolofon"
    r"|credits|crediti|cr[eé]ditos|generique"
    r"|newsletter|mailing\s+list|join\s+(?:the|our)\s+newsletter"
    r"|bonus\s+content|extras|contenu(?:s)?\s+bonus"
    r"|continue\s+reading|keep\s+reading"
    r"|sneak\s+peek|sneak\s+preview|preview\s+of"
    r"|excerpt\s+from|extrait\s+de|estratto\s+da"
    r"|coming\s+soon|prossimamente|pr[oó]ximamente|bient[oô]t"

    # ── Reading guides / Discussion / Book club ────────────────────────────
    r"|reading\s+group\s+guide|book\s+club\s+guide"
    r"|discussion\s+(?:guide|questions)|questions\s+for\s+discussion"
    r"|guida\s+alla\s+lettura|gruppo\s+di\s+lettura"
    r"|gu[ií]a\s+de\s+lectura|preguntas\s+para\s+la\s+discusi[oó]n"
    r"|guide\s+de\s+lecture|club\s+de\s+lecture"
    r"|leitfaden\s+f[üu]r\s+lesegruppen"

    # ── Bibliography / References / Sources / Notes ────────────────────────
    r"|bibliography|bibliografia|bibliographie|bibliograf[ií]a"
    r"|references|referencias|r[eé]f[eé]rences|riferimenti"
    r"|sources|fonti|fuentes|quellen"
    r"|works\s+cited|opere\s+citate|obras\s+citadas"
    r"|endnotes|footnotes|notes\s+on\s+sources|note\s+(?:al\s+)?testo"

    # ── Index / Glossary / Appendix ────────────────────────────────────────
    r"|^\s*index\s*$|^\s*indice\s*$|^\s*[íi]ndice\s*$|^\s*sachregister\s*$"
    r"|glossary|glossario|glossaire|glosario|gloss[áa]rio|glossar"
    r"|appendix|appendi(?:ce|ces|x)|apendice|ap[eê]ndice|anhang"

    # ── About the publisher / Imprint ──────────────────────────────────────
    r"|about\s+the\s+publisher|sull['\u2019]?\s*editor[ei]"
    r"|acerca\s+del\s+editor|sobre\s+a\s+editora"
    r"|[üu]ber\s+den\s+verlag"

    # ── Recipe sections (end-of-book bonus) ────────────────────────────────
    r"|ricettario|ricette|recipe(?:s)?\s+(?:from|index|section)|from\s+the\s+kitchen"
    r"|ingredienti\s*$|ingredients\s*$|recettes|recetas|rezepte"

    # ── Maps / Character lists (when at end as reference) ──────────────────
    r"|cast\s+of\s+characters|dramatis\s+personae|character\s+list"
    r"|elenco\s+dei\s+personaggi|personaggi\s+principali"
    r"|liste\s+des\s+personnages|personajes\s+principales"

    # ── Translator info ────────────────────────────────────────────────────
    r"|about\s+the\s+translator|sul\s+traduttore|sobre\s+el\s+traductor"

    # ── Reviews / Praise / Blurbs (often at end too) ───────────────────────
    r"|praise\s+for|reviews\s+of|critica\s+per|elogi\s+per"
    r"|lo\s+han\s+dicho|han\s+dit"

    # ── Meet the author / Author Q&A ───────────────────────────────────────
    r"|meet\s+the\s+author|conoce\s+al\s+autor|incontra\s+l['\u2019]?\s*autore"

    # ── Title pages / divider / blank-style markers (rare but seen) ────────
    r"|^\s*pagine\s+(?:da\s+riempire|bianche)\s*$"
    r"|^\s*pages?\s+blanches?\s*$"
    r"|^\s*p[áa]ginas?\s+(?:en\s+blanco|para\s+rellenar)\s*$"
    r"|^\s*blank\s+pages?\s*$"

    r")\b",
    re.MULTILINE,
)

# Filename keywords for EPUB items (supplements text detection).
# These are matched as substrings in normalized filenames.
# Keep them long enough to avoid false positives — minimum 5 chars typically.
_BACK_MATTER_FILE_KEYWORDS = {
    # Acknowledgments (multilingual roots)
    "acknowledgment", "acknowledgement", "acknowledgments", "acknowledgements",
    "ringraziamenti", "ringraziament",
    "remerciements", "remerciement",
    "agradecimientos", "agradecimiento",
    "agradecimentos", "agradecimento",
    "danksagung", "dankwoord",
    "podziekowania", "podziękowania",
    "thanks_to", "thank_you",

    # Afterword / Postscript
    "afterword", "afterwords",
    "postface", "postfazione", "posfacio", "nawoord", "nachwort",
    "etterord", "efterord", "poslowie",

    # Back matter generic
    "backmatter", "back_matter", "back-matter",
    "endmatter", "end_matter", "end-matter",
    "frontmatter",  # Sometimes mislabeled

    # Bibliography / References
    "bibliography", "bibliografia", "bibliographie", "bibliografie",
    "references", "referencias", "riferimenti",

    # Colophon / Credits / Imprint
    "colophon", "colofon", "kolofon",
    "credits", "crediti", "creditos", "credito",
    "imprint", "impressum", "frontespizio_legale",

    # About the author
    "about_the_author", "about-the-author", "abouttheauthor",
    "about_author", "aboutauthor",
    "author_bio", "author-bio", "authorbio",
    "biografia", "biografie",
    "gli_autori", "gli-autori", "lautore", "l_autore",
    "sobre_el_autor", "sobre_o_autor",
    "about_the_translator", "about_the_publisher",

    # Also by
    "also_by", "also-by", "alsoby",
    "other_books", "other-books", "otherbooks",
    "other_works", "otherworks",
    "altre_opere", "altreopere",
    "dello_stesso", "dellostesso",
    "del_mismo_autor",

    # Copyright / Legal
    "copyright", "_legal", "/legal", "copyrightpage",

    # Newsletter / Marketing
    "newsletter", "signup", "sign_up", "mailinglist", "mailing_list",
    "bonus", "bonuscontent", "bonus_content",
    "preview", "sneak_peek", "sneakpeek",
    "extras",

    # Interview / Q&A
    "interview", "intervista", "entrevista", "entretien", "gespraech",
    "q_and_a", "qanda", "qa_with",

    # Glossary / Index / Appendix
    "glossary", "glossario", "glossaire", "glosario", "glossar",
    "appendix", "appendice", "apendice", "anhang",
    # Note: "index" alone is too short and conflicts with index.html
    # We use index.htm/html separately below

    # Reading guide / Discussion
    "readinggroup", "reading_group", "readingguide", "reading_guide",
    "bookclub", "book_club",
    "discussion", "discussionguide",
    "guida_lettura", "guidalettura",

    # Recipe sections
    "ricettario", "ricetta", "ricette",
    "recipe", "recipes", "recetas", "recettes", "rezepte",

    # Cast / Characters list (end-of-book reference)
    "dramatis_personae", "dramatispersonae",
    "cast_of_characters", "castofcharacters",
    "character_list", "characterlist",
    "personaggi",

    # The End markers (rare as filenames but worth including)
    "the_end", "theend", "thend",
    "fine_libro", "finelibro",
}

# Filenames that look like generic landing pages (often back matter)
# These are EXACT or strict-prefix matches, not substring
_BACK_MATTER_EXACT_FILENAMES = {
    "index.html", "index.xhtml", "index.htm",
    "toc.html", "toc.xhtml",  # Table of contents at end is unusual but possible
}


def _is_back_matter_section(text: str, item_name: str = "") -> bool:
    """
    Check if a single section is back-matter.
    Only called on tail sections (never on middle-of-book content).

    Detection signals (any one is enough):
    1. Filename matches an exact back-matter filename
    2. Filename contains a back-matter keyword as a substring
    3. Section text starts with a back-matter heading
    """
    if item_name:
        # Get just the filename part if path-like
        base_name = item_name.lower().rsplit("/", 1)[-1].rsplit("\\", 1)[-1]

        # Exact filename match (e.g. "index.html")
        if base_name in _BACK_MATTER_EXACT_FILENAMES:
            return True

        # Substring keyword match against normalized filename
        # Normalize: replace separators with spaces for word-boundary safety
        name_normalized = base_name.replace(".", " ").replace("_", " ").replace("-", " ")
        # Also keep an underscore version for keywords that use underscores
        name_with_separators = item_name.lower()
        for kw in _BACK_MATTER_FILE_KEYWORDS:
            # Try both normalized and raw forms (some keywords have underscores)
            if kw in name_with_separators or kw.replace("_", " ") in name_normalized:
                return True

    # Check text heading — use RAW text (not word-joined) to preserve line breaks
    # for MULTILINE regex. First ~600 chars covers most headings.
    opening = text[:600]
    if _BACK_MATTER_HEADINGS.search(opening):
        return True

    return False


_BARE_HEADING = re.compile(r"^(?:\d{1,3}|[IVXLCDM]{1,5})\.?$")


def _is_story_section(text: str) -> bool:
    """
    Check if a section is definitively story content.
    If True, we MUST stop trimming — this is narrative.
    """
    # Use raw text to preserve line breaks for MULTILINE regex
    opening = text[:400]
    if _STORY_MARKERS.search(opening):
        return True

    # Fallback: bare-numeric chapter heading as the FIRST line.
    # Catches memoirs/novels that title chapters with just "1", "2", "II"
    # without the word "Chapter" — a popular minimalist convention.
    # Restricted to the first non-blank line so it can't false-trigger
    # on numbers appearing mid-text.
    stripped = text.lstrip()
    if stripped:
        first_line = stripped.split("\n", 1)[0].strip()
        if first_line and _BARE_HEADING.match(first_line):
            return True

    return False


def _trim_back_matter(pages: list[dict]) -> tuple[list[dict], list[str]]:
    """
    Scan backwards from the end of the book and remove back-matter sections.
    Stops the moment it encounters story content.

    Returns (filtered_pages, list_of_skipped_descriptions).

    RULES:
    - Scan from the last section backwards
    - If a section is detected as back-matter → mark for removal, continue
    - If a section looks like story content (chapter/epilogue markers) → STOP
    - If a section is ambiguous BUT very short (< 100 words) AND we've already
      trimmed at least one confirmed back-matter section → treat as a divider
      page / title card and trim it too (continue scanning)
    - If a section is ambiguous with substantial content → STOP
      (conservative: we'd rather include non-story content than skip story)
    - Never remove more than 40% of sections (safety cap)
    """
    if len(pages) < 3:
        return pages, []

    TAIL_MIN_WORDS = 100  # Ambiguous sections below this are treated as divider pages

    max_removable = max(1, int(len(pages) * 0.4))  # Safety cap
    skipped = []
    trim_from = len(pages)  # Index to trim from (exclusive)
    confirmed_back_matter = 0  # How many confirmed back-matter sections we've trimmed

    for i in range(len(pages) - 1, -1, -1):
        if len(pages) - trim_from >= max_removable:
            break  # Safety cap reached

        section = pages[i]
        text = section["text"]
        item_name = section.get("item_name", "")
        word_count = len(text.split())

        # If this section has clear story markers, STOP — we've reached the narrative
        if _is_story_section(text):
            break

        # If this section has clear back-matter signals, mark it for removal
        if _is_back_matter_section(text, item_name):
            trim_from = i
            confirmed_back_matter += 1
            skipped.append(item_name or f"section {i+1} ({word_count} words)")
            continue

        # Ambiguous section — no clear signals either way.
        # If it's very short AND we've already confirmed back-matter after it,
        # it's almost certainly a divider page / title card (not story content).
        if word_count < TAIL_MIN_WORDS and confirmed_back_matter > 0:
            trim_from = i
            skipped.append(
                f"{item_name or f'section {i+1}'} ({word_count} words, low-content)")
            continue

        # Ambiguous section with substantial content — STOP.
        # We'd rather include non-story content than risk skipping story.
        break

    filtered = pages[:trim_from]
    skipped.reverse()  # Put in forward order for logging
    return filtered, skipped


def _trim_body_boundaries(pages: list[dict], phase1_trimmed: int) -> tuple[list[dict], list[str]]:
    """
    Phase 2: Trim content OUTSIDE the main story body using story markers.

    After Phase 1 (heading/filename-based back-matter trim), this pass finds
    the first and last sections with story markers (chapter numbers, prologue,
    epilogue) and trims everything outside those boundaries.

    FRONT TRIM: Sections before the first story marker (book blurbs, author
    info, title pages). Capped at 5 sections max.

    TAIL TRIM: Sections between the last story marker and the Phase 1 trim
    point. This catches bonus stories, short stories, and other content that
    doesn't have back-matter headings but isn't part of the main narrative.

    SAFETY RULES:
    - Only runs if Phase 1 already trimmed something (confirms the book has
      non-body content — if Phase 1 found nothing, Phase 2 stays quiet)
    - Never trims sections WITH story markers (chapters/epilogues always safe)
    - Front trim capped at 5 sections (most books have 1-3 front-matter pages)
    - Total trim (Phase 1 + Phase 2) never exceeds 40% of original sections
    - If no story markers found at all, does nothing

    Args:
        pages: sections remaining after Phase 1 trim
        phase1_trimmed: how many sections Phase 1 already removed

    Returns:
        (filtered_pages, list_of_skipped_descriptions)
    """
    if phase1_trimmed == 0 or len(pages) < 3:
        return pages, []

    skipped = []
    original_total = len(pages) + phase1_trimmed
    max_total_removable = max(1, int(original_total * 0.4))
    remaining_budget = max_total_removable - phase1_trimmed

    if remaining_budget <= 0:
        return pages, []

    # ── Find first and last story markers ─────────────────────────────────
    first_story_idx = -1
    last_story_idx = -1
    for i in range(len(pages)):
        if _is_story_section(pages[i]["text"]):
            if first_story_idx < 0:
                first_story_idx = i
            last_story_idx = i

    # No story markers found — can't determine body boundaries
    if first_story_idx < 0:
        return pages, []

    # ── Front trim: sections before the first story marker ────────────────
    # Only trim if first marker is within the first 6 sections (positions 0-5)
    # and we have budget remaining
    front_trim = 0
    if first_story_idx > 0 and first_story_idx <= 5:
        front_trim = min(first_story_idx, remaining_budget)
        for i in range(front_trim):
            section = pages[i]
            item_name = section.get("item_name", "")
            word_count = len(section["text"].split())
            skipped.append(
                f"{item_name or f'section {i+1}'} ({word_count}w, front matter)")
        remaining_budget -= front_trim

    # ── Tail trim: sections after the last story marker ───────────────────
    # Adjust last_story_idx for front trim offset
    adjusted_last_story = last_story_idx - front_trim
    tail_start = adjusted_last_story + 1
    pages_after_front = pages[front_trim:]

    tail_trim_count = 0
    if tail_start < len(pages_after_front) and remaining_budget > 0:
        gap_sections = pages_after_front[tail_start:]
        trim_count = min(len(gap_sections), remaining_budget)

        for i in range(trim_count):
            section = gap_sections[i]
            item_name = section.get("item_name", "")
            word_count = len(section["text"].split())
            skipped.append(
                f"{item_name or 'unnamed'} ({word_count}w, outside body)")
            tail_trim_count += 1

        pages_after_front = pages_after_front[:tail_start]

    result = pages_after_front
    return result, skipped


def extract_text(file_path: str) -> ExtractionResult:
    """
    Main entry point — detects file type and routes to the right extractor.
    Supports .pdf and .epub files.
    """
    ext = os.path.splitext(file_path)[1].lower()

    if ext == ".pdf":
        return extract_text_from_pdf(file_path)
    elif ext == ".epub":
        return extract_text_from_epub(file_path)
    else:
        raise ValueError(
            f"Unsupported file format: '{ext}'. Supported: .pdf, .epub"
        )


def extract_text_from_pdf(pdf_path: str) -> ExtractionResult:
    """
    Extract all text from a PDF and segment into chunks.

    Returns the chunks PLUS the opening/closing text for the holistic pass.
    """
    import fitz  # pymupdf

    doc = fitz.open(pdf_path)
    warnings: list[str] = []

    # ── Extract PDF metadata ──────────────────────────────────────────────
    pdf_meta = _extract_pdf_metadata(doc)

    # ── Page-by-page extraction ───────────────────────────────────────────
    pages: list[dict] = []
    for page_num in range(len(doc)):
        page = doc[page_num]
        text = page.get_text("text")
        if text.strip():
            pages.append({"page": page_num + 1, "text": text})

    if not pages:
        warnings.append("No text extracted — PDF may be scanned/image-based")
        total_pages = len(doc)
        doc.close()
        return ExtractionResult(
            full_text="",
            chunks=[],
            total_pages=total_pages,
            total_words=0,
            title_guess=pdf_meta.pdf_title or "Unknown",
            author_guess=pdf_meta.pdf_author or "Unknown",
            pdf_metadata=pdf_meta,
            opening_text="",
            closing_text="",
            extraction_warnings=warnings,
        )

    full_text = "\n\n".join(p["text"] for p in pages)
    total_words = len(full_text.split())
    total_pages = pages[-1]["page"]

    # ── Guess metadata (combine PDF metadata + text heuristics) ───────────
    title_guess, author_guess = _guess_metadata(pages[:3], pdf_meta)

    # ── Extract opening and closing text for Pass 2 ───────────────────────
    opening_text = _extract_opening(pages, target_words=1500)
    closing_text = _extract_closing(pages, target_words=1500)

    # ── Segment into chunks ───────────────────────────────────────────────
    chunks = _segment_by_chapters(pages)
    if not chunks:
        logger.info("No chapters detected — using windowed chunking")
        chunks = _segment_by_window(pages, target_words=4000, overlap_words=300)

    # ── Merge small consecutive chunks (target ~10k words, cap at 15k) ────
    pre_merge_count = len(chunks)
    chunks = _merge_small_chunks(chunks, target_words=8000, max_words=12000)
    if len(chunks) < pre_merge_count:
        logger.info(f"  Merged {pre_merge_count} chapter chunks into {len(chunks)} "
                     f"larger chunks (target ~8k words each)")

    # ── Enforce max chunk size (split any chunk larger than the cap) ──────
    final_chunks: list[TextChunk] = []
    max_words = 12000
    for chunk in chunks:
        if chunk.word_count > max_words:
            sub_chunks = _split_large_chunk(chunk, max_words=max_words)
            final_chunks.extend(sub_chunks)
        else:
            final_chunks.append(chunk)

    # Re-index
    for i, chunk in enumerate(final_chunks):
        chunk.index = i

    doc.close()

    return ExtractionResult(
        full_text=full_text,
        chunks=final_chunks,
        total_pages=total_pages,
        total_words=total_words,
        title_guess=title_guess,
        author_guess=author_guess,
        pdf_metadata=pdf_meta,
        opening_text=opening_text,
        closing_text=closing_text,
        extraction_warnings=warnings,
    )


def _extract_pdf_metadata(doc) -> PDFMetadata:
    """Extract metadata from PDF document properties."""
    meta = doc.metadata or {}
    publish_year = None

    # Try to extract year from various fields
    for date_field in ["creationDate", "modDate"]:
        date_str = meta.get(date_field, "")
        if date_str:
            # PDF dates: D:20210315... or just 2021...
            year_match = re.search(r"(\d{4})", date_str)
            if year_match:
                year = int(year_match.group(1))
                if 1400 < year < 2100:
                    publish_year = year
                    break

    # Publisher often in "producer" or "creator" fields
    publisher = None
    producer = meta.get("producer", "")
    creator = meta.get("creator", "")
    # Filter out software names (common false positives)
    software_keywords = ["pdf", "adobe", "microsoft", "latex", "tex",
                         "calibre", "ghostscript", "quartz", "mac os",
                         "openoffice", "libreoffice", "word", "chrome"]
    for candidate in [producer, creator]:
        if candidate and not any(kw in candidate.lower() for kw in software_keywords):
            publisher = candidate
            break

    return PDFMetadata(
        pdf_title=meta.get("title") or None,
        pdf_author=meta.get("author") or None,
        publisher=publisher,
        publish_year=publish_year,
        creator=creator or None,
        subject=meta.get("subject") or None,
    )


def _extract_opening(pages: list[dict], target_words: int = 1500) -> str:
    """Extract the opening ~target_words of the book."""
    words_collected = 0
    parts: list[str] = []
    for p in pages:
        parts.append(p["text"])
        words_collected += len(p["text"].split())
        if words_collected >= target_words:
            break
    text = "\n\n".join(parts)
    words = text.split()
    return " ".join(words[:target_words])


def _extract_closing(pages: list[dict], target_words: int = 1500) -> str:
    """Extract the closing ~target_words of the book."""
    words_collected = 0
    parts: list[str] = []
    for p in reversed(pages):
        parts.insert(0, p["text"])
        words_collected += len(p["text"].split())
        if words_collected >= target_words:
            break
    text = "\n\n".join(parts)
    words = text.split()
    return " ".join(words[-target_words:])


def _guess_metadata(
    first_pages: list[dict], pdf_meta: PDFMetadata
) -> tuple[str, str]:
    """Combine PDF metadata + text heuristics for best guess."""
    # Prefer PDF metadata if available and non-generic
    title = pdf_meta.pdf_title
    author = pdf_meta.pdf_author

    if not title or len(title) < 2:
        title = None
    if not author or len(author) < 2:
        author = None

    # Fall back to text heuristics
    if not title or not author:
        combined = "\n".join(p["text"] for p in first_pages[:2])
        lines = [ln.strip() for ln in combined.split("\n") if ln.strip()]

        if not title and lines:
            candidates = [ln for ln in lines[:10] if 3 < len(ln) < 100]
            if candidates:
                title = max(candidates, key=len)

        if not author:
            for ln in lines[:15]:
                if ln.lower().startswith("by "):
                    author = ln[3:].strip()
                    break
                if re.match(r"^[A-Z][a-z]+ [A-Z][a-z]+$", ln):
                    author = ln
                    break

    return title or "Unknown", author or "Unknown"


def _segment_by_chapters(pages: list[dict]) -> list[TextChunk]:
    """Split on chapter boundaries."""
    full_with_markers = ""
    page_offsets: list[tuple[int, int, int]] = []

    for p in pages:
        start = len(full_with_markers)
        full_with_markers += p["text"] + "\n\n"
        end = len(full_with_markers)
        page_offsets.append((start, end, p["page"]))

    breaks: list[tuple[int, str]] = []
    for pattern in CHAPTER_PATTERNS:
        for match in pattern.finditer(full_with_markers):
            breaks.append((match.start(), match.group().strip()))

    if len(breaks) < 2:
        return []

    breaks.sort(key=lambda x: x[0])
    deduped: list[tuple[int, str]] = [breaks[0]]
    for offset, label in breaks[1:]:
        if offset - deduped[-1][0] > 200:
            deduped.append((offset, label))
    breaks = deduped

    chunks: list[TextChunk] = []
    for i, (offset, label) in enumerate(breaks):
        end_offset = breaks[i + 1][0] if i + 1 < len(breaks) else len(full_with_markers)
        text = full_with_markers[offset:end_offset].strip()
        p_start = _offset_to_page(offset, page_offsets)
        p_end = _offset_to_page(end_offset - 1, page_offsets)

        chunks.append(TextChunk(
            index=i, label=label, text=text,
            page_start=p_start, page_end=p_end,
        ))

    return chunks


def _segment_by_window(
    pages: list[dict],
    target_words: int = 4000,
    overlap_words: int = 300,
) -> list[TextChunk]:
    """Fixed-size window chunking with overlap."""
    all_words: list[tuple[str, int]] = []
    for p in pages:
        for word in p["text"].split():
            all_words.append((word, p["page"]))

    chunks: list[TextChunk] = []
    i = 0
    chunk_idx = 0
    while i < len(all_words):
        end = min(i + target_words, len(all_words))
        chunk_words = all_words[i:end]
        text = " ".join(w for w, _ in chunk_words)
        p_start = chunk_words[0][1]
        p_end = chunk_words[-1][1]

        chunks.append(TextChunk(
            index=chunk_idx,
            label=f"Pages {p_start}-{p_end}",
            text=text,
            page_start=p_start,
            page_end=p_end,
        ))

        chunk_idx += 1
        i = end - overlap_words if end < len(all_words) else end

    return chunks


def _split_large_chunk(chunk: TextChunk, max_words: int) -> list[TextChunk]:
    """Split an oversized chunk."""
    words = chunk.text.split()
    sub_chunks: list[TextChunk] = []
    i = 0
    part = 1
    while i < len(words):
        end = min(i + max_words, len(words))
        text = " ".join(words[i:end])
        sub_chunks.append(TextChunk(
            index=0, label=f"{chunk.label} (part {part})",
            text=text, page_start=chunk.page_start, page_end=chunk.page_end,
        ))
        part += 1
        i = end
    return sub_chunks


def _merge_small_chunks(chunks: list[TextChunk], target_words: int = 8000,
                         max_words: int = 12000) -> list[TextChunk]:
    """
    Greedy merger of consecutive chunks. Combines small chunks (typically
    short chapters) into larger groups so the analysis pipeline pays
    less per-call scaffolding overhead.

    Rules:
      - Walk chunks in order; build groups of consecutive chunks.
      - A chunk is added to the current group only if:
          combined_words + chunk.word_count <= max_words
        AND
          combined_words < target_words (don't keep merging once target met)
      - Single chunks already >= target_words pass through alone (no merging).
      - Single chunks already > max_words also pass through (the downstream
        cap-check splitter handles them).
      - Order is preserved — chunks never reorder.

    Args:
        chunks: input list of TextChunks (already chapter-segmented)
        target_words: ideal merged-chunk size (soft target)
        max_words: hard cap; merged chunks never exceed this

    Returns:
        New list of TextChunks. Indices are not renumbered (caller does that).
    """
    if not chunks:
        return chunks

    merged: list[TextChunk] = []
    current_group: list[TextChunk] = []
    current_words = 0

    def flush_group():
        nonlocal current_group, current_words
        if not current_group:
            return
        if len(current_group) == 1:
            merged.append(current_group[0])
        else:
            first = current_group[0]
            last = current_group[-1]
            label = (first.label if first.label == last.label
                     else f"{first.label}–{last.label}")
            text = "\n\n".join(c.text for c in current_group)
            merged.append(TextChunk(
                index=0,  # caller renumbers
                label=label,
                text=text,
                page_start=first.page_start,
                page_end=last.page_end,
            ))
        current_group = []
        current_words = 0

    for c in chunks:
        # If this chunk alone meets/exceeds target, flush + emit alone
        if c.word_count >= target_words:
            flush_group()
            merged.append(c)
            continue

        # If adding this chunk would exceed cap, flush first
        if current_words + c.word_count > max_words:
            flush_group()
            current_group.append(c)
            current_words = c.word_count
            continue

        # If current group already at/over target, close and start new
        if current_words >= target_words:
            flush_group()
            current_group.append(c)
            current_words = c.word_count
            continue

        # Otherwise, add to current group
        current_group.append(c)
        current_words += c.word_count

    flush_group()
    return merged


def _offset_to_page(offset: int, page_offsets: list[tuple[int, int, int]]) -> int:
    for start, end, page in page_offsets:
        if start <= offset < end:
            return page
    return page_offsets[-1][2] if page_offsets else 1


# ═══════════════════════════════════════════════════════════════════════════════
# EPUB EXTRACTION
# ═══════════════════════════════════════════════════════════════════════════════

def extract_text_from_epub(epub_path: str) -> ExtractionResult:
    """
    Extract all text from an EPUB and segment into chunks.
    Uses ebooklib for EPUB parsing and BeautifulSoup for HTML→text.
    """
    from ebooklib import epub, ITEM_DOCUMENT
    from bs4 import BeautifulSoup

    book = epub.read_epub(epub_path, options={"ignore_ncx": True})
    warnings: list[str] = []

    # ── Extract EPUB metadata ─────────────────────────────────────────────
    epub_meta = _extract_epub_metadata(book)

    # ── Extract text from each HTML chapter ───────────────────────────────
    raw_pages: list[dict] = []
    page_num = 0

    for item in book.get_items_of_type(ITEM_DOCUMENT):
        try:
            html_content = item.get_content().decode("utf-8", errors="replace")
        except Exception:
            continue

        soup = BeautifulSoup(html_content, "html.parser")

        # Remove script/style tags
        for tag in soup(["script", "style", "head"]):
            tag.decompose()

        text = soup.get_text(separator="\n", strip=True)

        # Skip near-empty sections (copyright pages, blank chapters, etc.)
        if not text.strip() or len(text.split()) <= 20:
            continue

        item_name = getattr(item, "file_name", "") or getattr(item, "id", "") or ""
        page_num += 1
        raw_pages.append({"page": page_num, "text": text, "item_name": item_name})

    # ── Phase 1: Trim confirmed back-matter from the tail ───────────────
    # Scans backwards from end, stops at first story section or ambiguous section
    pages, skipped_back_matter = _trim_back_matter(raw_pages)

    if skipped_back_matter:
        logger.info(f"  Trimmed {len(skipped_back_matter)} back-matter section(s) "
                     f"from end: {', '.join(skipped_back_matter)}")
        warnings.append(f"Trimmed {len(skipped_back_matter)} back-matter section(s) from end")

    # ── Phase 2: Trim content outside the story body ──────────────────────
    # Uses story markers (chapter numbers, prologue, epilogue) to find the
    # actual narrative boundaries. Trims front matter and bonus content.
    pages, skipped_body = _trim_body_boundaries(pages, len(skipped_back_matter))

    if skipped_body:
        logger.info(f"  Trimmed {len(skipped_body)} non-body section(s): "
                     f"{', '.join(skipped_body)}")
        warnings.append(f"Trimmed {len(skipped_body)} non-body section(s)")

    if not pages:
        warnings.append("No text extracted from EPUB")
        return ExtractionResult(
            full_text="",
            chunks=[],
            total_pages=0,
            total_words=0,
            title_guess=epub_meta.pdf_title or "Unknown",
            author_guess=epub_meta.pdf_author or "Unknown",
            pdf_metadata=epub_meta,
            opening_text="",
            closing_text="",
            extraction_warnings=warnings,
        )

    full_text = "\n\n".join(p["text"] for p in pages)
    total_words = len(full_text.split())
    total_pages = len(pages)

    # ── Guess metadata ────────────────────────────────────────────────────
    title_guess, author_guess = _guess_metadata(pages[:3], epub_meta)

    # ── Opening and closing text for Pass 2 ───────────────────────────────
    opening_text = _extract_opening(pages, target_words=1500)
    closing_text = _extract_closing(pages, target_words=1500)

    # ── Segment into chunks ───────────────────────────────────────────────
    # EPUBs have natural chapter boundaries built into the format — one HTML
    # file per chapter. Prefer those over running regex chapter detection on
    # the merged text, because:
    #   (a) Many EPUBs use bare-number chapter labels ("1", "2", ...) that
    #       text-pattern detection doesn't recognize.
    #   (b) Many EPUBs embed TOC navigation strips or accessibility text
    #       ("Chapter 1", "Chapter 2", ...) that creates *false* break points
    #       far from the real chapter starts — leading to tiny/empty chunks.
    # If the EPUB has at least 5 sections after trimming, its structure is
    # trustworthy. Otherwise fall back to text-pattern detection (handles
    # single-file EPUBs where all chapters live in one HTML document).

    chunks: list[TextChunk] = []

    if len(pages) >= 5:
        # EPUB has clear chapter structure — use it directly.
        merged_pages = _merge_short_epub_sections(pages, min_words=500)
        if len(merged_pages) >= 2:
            for i, p in enumerate(merged_pages):
                chunks.append(TextChunk(
                    index=i,
                    label=f"Section {i + 1}",
                    text=p["text"],
                    page_start=p["page"],
                    page_end=p["page"],
                ))

    if not chunks:
        # Single-file EPUB (or very short book) — try text-pattern chapter
        # detection on the merged text.
        chunks = _segment_by_chapters(pages)

    if not chunks:
        # Last resort — windowed chunking.
        logger.info("EPUB has no clear sections — using windowed chunking")
        chunks = _segment_by_window(pages, target_words=4000, overlap_words=300)

    # ── Merge small consecutive chunks (target ~10k words, cap at 15k) ────
    pre_merge_count = len(chunks)
    chunks = _merge_small_chunks(chunks, target_words=8000, max_words=12000)
    if len(chunks) < pre_merge_count:
        logger.info(f"  Merged {pre_merge_count} chapter chunks into {len(chunks)} "
                     f"larger chunks (target ~8k words each)")

    # ── Enforce max chunk size (split any chunk larger than the cap) ──────
    final_chunks: list[TextChunk] = []
    max_words = 12000
    for chunk in chunks:
        if chunk.word_count > max_words:
            sub_chunks = _split_large_chunk(chunk, max_words=max_words)
            final_chunks.extend(sub_chunks)
        else:
            final_chunks.append(chunk)

    # Re-index
    for i, chunk in enumerate(final_chunks):
        chunk.index = i

    return ExtractionResult(
        full_text=full_text,
        chunks=final_chunks,
        total_pages=total_pages,
        total_words=total_words,
        title_guess=title_guess,
        author_guess=author_guess,
        pdf_metadata=epub_meta,  # Reuses same dataclass
        opening_text=opening_text,
        closing_text=closing_text,
        extraction_warnings=warnings,
    )


def _extract_epub_metadata(book) -> PDFMetadata:
    """Extract metadata from EPUB document properties."""
    title = None
    author = None
    publisher = None
    publish_year = None
    language = None
    isbn = None

    # Title
    try:
        titles = book.get_metadata("DC", "title")
        if titles:
            title = titles[0][0]
    except Exception:
        pass

    # Author
    try:
        creators = book.get_metadata("DC", "creator")
        if creators:
            author = creators[0][0]
    except Exception:
        pass

    # Publisher
    try:
        publishers = book.get_metadata("DC", "publisher")
        if publishers:
            publisher = publishers[0][0]
    except Exception:
        pass

    # Date / year
    try:
        dates = book.get_metadata("DC", "date")
        if dates:
            date_str = dates[0][0]
            year_match = re.search(r"(\d{4})", date_str)
            if year_match:
                year = int(year_match.group(1))
                if 1400 < year < 2100:
                    publish_year = year
    except Exception:
        pass

    # Language (e.g. "en", "it", "fr", "en-US")
    try:
        languages = book.get_metadata("DC", "language")
        if languages:
            language = languages[0][0].strip()
    except Exception:
        pass

    # ISBN — stored in DC:identifier, often prefixed with "isbn:" or "urn:isbn:"
    try:
        identifiers = book.get_metadata("DC", "identifier")
        for ident in identifiers:
            val = ident[0].strip() if ident[0] else ""
            # Check for ISBN prefix
            val_lower = val.lower()
            if "isbn" in val_lower:
                # Extract just the digits/hyphens
                isbn_match = re.search(r"(\d[\d\-]{8,16}\d)", val)
                if isbn_match:
                    isbn = isbn_match.group(1)
                    break
            # Also check raw value — some EPUBs just put the ISBN as the identifier
            elif re.match(r"^(97[89])?\d{9}[\dXx]$", val.replace("-", "")):
                isbn = val
                break
    except Exception:
        pass

    return PDFMetadata(
        pdf_title=title,
        pdf_author=author,
        publisher=publisher,
        publish_year=publish_year,
        creator=None,
        subject=None,
        language=language,
        isbn=isbn,
    )


def _merge_short_epub_sections(
    pages: list[dict], min_words: int = 500
) -> list[dict]:
    """
    Merge adjacent EPUB sections that are very short.
    Prevents having 50 tiny chunks from books with many small HTML files.
    """
    if not pages:
        return pages

    merged: list[dict] = []
    current = {"page": pages[0]["page"], "text": pages[0]["text"]}

    for p in pages[1:]:
        current_words = len(current["text"].split())
        next_words = len(p["text"].split())

        if current_words < min_words or next_words < min_words:
            # Merge with current
            current["text"] = current["text"] + "\n\n" + p["text"]
        else:
            merged.append(current)
            current = {"page": p["page"], "text": p["text"]}

    merged.append(current)
    return merged