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
    """Metadata extracted from PDF document properties."""
    pdf_title: Optional[str] = None
    pdf_author: Optional[str] = None
    publisher: Optional[str] = None
    publish_year: Optional[int] = None
    creator: Optional[str] = None
    subject: Optional[str] = None


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
_STORY_MARKERS = re.compile(
    r"(?i)^\s*("
    # Chapter markers (multilingual)
    r"chapter\s+\w|capitolo\s+\w|chapitre\s+\w|cap[ií]tulo\s+\w|kapitel\s+\w"
    r"|cap\.\s*\w"
    # Part markers
    r"|part\s+\w|parte\s+\w|partie\s+\w|teil\s+\w"
    # Numbered sections (1. / I. / 01.)
    r"|\d+\.\s+[A-Z]|[IVXLCDM]+\.\s+[A-Z]"
    # Prologue/Epilogue markers (multilingual) — these ARE story content
    r"|prologue|prologo|pr[oó]logo|prolog"
    r"|epilogue|epilogo|ep[ií]logo|epilog"
    r")\b",
    re.MULTILINE,
)

# High-confidence back-matter headings (multilingual).
# These are terms that essentially NEVER appear as chapter titles in fiction
# or non-fiction narrative. Each group covers: EN, IT, FR, ES, DE, PT
_BACK_MATTER_HEADINGS = re.compile(
    r"(?i)^\s*("
    # Acknowledgments
    r"acknowledg[e]?ments?|ringraziamenti|remerciements|agradecimientos"
    r"|danksagung|agradecimentos"
    # Copyright / legal
    r"|copyright|all\s+rights\s+reserved|tutti\s+i\s+diritti\s+riservati"
    r"|tous\s+droits\s+r[eé]serv[eé]s|todos\s+los\s+derechos\s+reservados"
    r"|alle\s+rechte\s+vorbehalten|todos\s+os\s+direitos\s+reservados"
    # About the author
    r"|about\s+the\s+author|sull['\u2019]?\s*autor[ei]|nota\s+sull['\u2019]?\s*autor[ei]"
    r"|[àa]\s+propos\s+de\s+l['\u2019]?\s*auteur|sobre\s+el\s+autor"
    r"|[üu]ber\s+d(?:en|ie)\s+autor(?:in)?|sobre\s+o\s+autor"
    r"|gli\s+autori|les\s+auteurs|los\s+autores"
    # Also by / other works
    r"|also\s+by|dello\s+stesso\s+autor[ei]|du\s+m[êe]me\s+auteur"
    r"|del\s+mismo\s+autor|vom\s+selben\s+autor|do\s+mesmo\s+autor"
    r"|other\s+(?:books|works)\s+by|altre\s+opere"
    # Author/editor notes (positioned here = back matter context)
    r"|author['\u2019]?s?\s+note|editor['\u2019]?s?\s+note"
    r"|nota\s+dell['\u2019]?\s*editor[ei]|note\s+de\s+l['\u2019]?\s*[eé]diteur"
    # Interviews
    r"|interview\s+with|intervista\s+con|entrevue\s+avec|entrevista\s+con"
    # Colophon / credits / newsletter
    r"|colophon|credits|crediti|newsletter|bonus\s+content"
    # Reading guides / discussion
    r"|reading\s+group\s+guide|discussion\s+(?:guide|questions)"
    r"|guida\s+alla\s+lettura|gu[ií]a\s+de\s+lectura"
    # Bibliography / references
    r"|bibliography|bibliografia|bibliographie|bibliograf[ií]a"
    # Glossary
    r"|glossary|glossario|glossaire|glosario"
    # Recipe sections (specific enough to be safe)
    r"|ricettario|recipe\s+(?:from|index)|from\s+the\s+kitchen"
    r"|ingredienti\s*$|ingredients\s*$"
    r")\b",
    re.MULTILINE,
)

# Filename keywords for EPUB items (supplements text detection)
_BACK_MATTER_FILE_KEYWORDS = {
    "acknowledgment", "acknowledgement", "afterword",
    "backmatter", "back_matter", "back-matter",
    "bibliography", "colophon", "credits",
    "about_the_author", "about-the-author", "abouttheauthor",
    "author_bio", "author-bio",
    "also_by", "also-by", "alsoby", "other_books", "other-books",
    "copyright", "legal", "imprint",
    "newsletter", "signup", "bonus",
    "interview", "intervista",
    "glossary", "glossario",
    "ringraziamenti", "remerciements", "agradecimientos",
    "ricettario", "recipe",
}


def _is_back_matter_section(text: str, item_name: str = "") -> bool:
    """
    Check if a single section is back-matter.
    Only called on tail sections (never on middle-of-book content).
    """
    # Check filename first (fast path)
    if item_name:
        name_lower = item_name.lower().replace(".", " ").replace("/", " ")
        for kw in _BACK_MATTER_FILE_KEYWORDS:
            if kw in name_lower:
                return True

    # Check text heading — use RAW text (not word-joined) to preserve line breaks
    # for MULTILINE regex. First ~600 chars covers most headings.
    opening = text[:600]
    if _BACK_MATTER_HEADINGS.search(opening):
        return True

    return False


def _is_story_section(text: str) -> bool:
    """
    Check if a section is definitively story content.
    If True, we MUST stop trimming — this is narrative.
    """
    # Use raw text to preserve line breaks for MULTILINE regex
    opening = text[:400]
    return bool(_STORY_MARKERS.search(opening))


def _trim_back_matter(pages: list[dict]) -> tuple[list[dict], list[str]]:
    """
    Scan backwards from the end of the book and remove back-matter sections.
    Stops the moment it encounters story content.

    Returns (filtered_pages, list_of_skipped_descriptions).

    RULES:
    - Scan from the last section backwards
    - If a section is detected as back-matter → mark for removal, continue
    - If a section looks like story content (chapter/epilogue markers) → STOP
    - If a section is ambiguous (no back-matter signals, no story signals) → STOP
      (conservative: we'd rather include non-story content than skip story)
    - Never remove more than 40% of sections (safety cap)
    """
    if len(pages) < 3:
        return pages, []

    max_removable = max(1, int(len(pages) * 0.4))  # Safety cap
    skipped = []
    trim_from = len(pages)  # Index to trim from (exclusive)

    for i in range(len(pages) - 1, -1, -1):
        if len(pages) - trim_from >= max_removable:
            break  # Safety cap reached

        section = pages[i]
        text = section["text"]
        item_name = section.get("item_name", "")

        # If this section has clear story markers, STOP — we've reached the narrative
        if _is_story_section(text):
            break

        # If this section has clear back-matter signals, mark it for removal
        if _is_back_matter_section(text, item_name):
            trim_from = i
            skipped.append(item_name or f"section {i+1} ({len(text.split())} words)")
            continue

        # Ambiguous section — no clear signals either way.
        # STOP here. We'd rather include a recipe than skip a story chapter.
        break

    filtered = pages[:trim_from]
    skipped.reverse()  # Put in forward order for logging
    return filtered, skipped


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

    # ── Enforce max chunk size ────────────────────────────────────────────
    final_chunks: list[TextChunk] = []
    max_words = 5000
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

    # ── Trim back-matter from the tail ────────────────────────────────────
    # Scans backwards from end, stops at first story section or ambiguous section
    pages, skipped_back_matter = _trim_back_matter(raw_pages)

    if skipped_back_matter:
        logger.info(f"  Trimmed {len(skipped_back_matter)} back-matter section(s) "
                     f"from end: {', '.join(skipped_back_matter)}")
        warnings.append(f"Trimmed {len(skipped_back_matter)} back-matter section(s) from end")

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
    # EPUBs have natural chapter boundaries from their HTML sections.
    # Each "page" in our list is already roughly a chapter.
    # If sections are very short, merge adjacent ones.
    # If sections are very long, split them.

    # First try chapter detection in the full text (same as PDF)
    chunks = _segment_by_chapters(pages)

    if not chunks:
        # Use EPUB sections as natural chapters
        # Merge very short adjacent sections (< 500 words)
        merged_pages = _merge_short_epub_sections(pages, min_words=500)

        if len(merged_pages) >= 2:
            chunks = []
            for i, p in enumerate(merged_pages):
                chunks.append(TextChunk(
                    index=i,
                    label=f"Section {i + 1}",
                    text=p["text"],
                    page_start=p["page"],
                    page_end=p["page"],
                ))
        else:
            # Fall back to windowed chunking
            logger.info("EPUB has no clear sections — using windowed chunking")
            chunks = _segment_by_window(pages, target_words=4000, overlap_words=300)

    # ── Enforce max chunk size ────────────────────────────────────────────
    final_chunks: list[TextChunk] = []
    max_words = 5000
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

    return PDFMetadata(
        pdf_title=title,
        pdf_author=author,
        publisher=publisher,
        publish_year=publish_year,
        creator=None,
        subject=None,
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