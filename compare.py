"""
Genius Book Analysis — Comparison Tool

Compares two analysis JSON files of the SAME book and shows exactly
what changed. Use this to track quality when optimizing for cost/speed.

Usage:
    python compare.py run1_analysis.json run2_analysis.json
    python compare.py baseline.json haiku_test.json --detailed

Typical workflow:
    1. Run baseline with Sonnet:     python run_analysis.py book.pdf
    2. Rename output:                mv book_analysis.json baseline.json
    3. Run with Haiku:               python run_analysis.py book.pdf --model claude-haiku-4-5-20251001
    4. Compare:                      python compare.py baseline.json book_analysis.json
"""

from __future__ import annotations
import argparse
import json
import sys


def load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def compare(a: dict, b: dict, detailed: bool = False) -> dict:
    """Compare two analysis outputs and return a diff report."""
    report = {
        "metadata_match": True,
        "rating_diffs": {},
        "themes_added": [],
        "themes_removed": [],
        "themes_common": [],
        "characters_added": [],
        "characters_removed": [],
        "characters_common": [],
        "categories_match": False,
        "flags_added": [],
        "flags_removed": [],
        "ending_match": True,
        "total_score": 0,  # 0-100 similarity score
    }

    # ── Metadata ──────────────────────────────────────────────────────────
    for field in ["title", "author", "genre"]:
        av = a.get("metadata", {}).get(field, "")
        bv = b.get("metadata", {}).get(field, "")
        if str(av).lower() != str(bv).lower():
            report["metadata_match"] = False

    # ── Ratings ───────────────────────────────────────────────────────────
    a_ratings = a.get("ratings", {})
    b_ratings = b.get("ratings", {})
    for key in ["tone", "readability", "violence", "age_target", "pace",
                "worldbuilding", "humor", "romance"]:
        av = a_ratings.get(key, 0)
        bv = b_ratings.get(key, 0)
        diff = bv - av
        if diff != 0:
            report["rating_diffs"][key] = {"a": av, "b": bv, "diff": diff}

    # ── Themes ────────────────────────────────────────────────────────────
    a_themes = {t["name"].lower() for t in a.get("themes", [])}
    b_themes = {t["name"].lower() for t in b.get("themes", [])}
    report["themes_added"] = sorted(b_themes - a_themes)
    report["themes_removed"] = sorted(a_themes - b_themes)
    report["themes_common"] = sorted(a_themes & b_themes)

    # Theme prominence diffs for common themes
    if detailed:
        a_proms = {t["name"].lower(): t["prominence"] for t in a.get("themes", [])}
        b_proms = {t["name"].lower(): t["prominence"] for t in b.get("themes", [])}
        report["theme_prominence_diffs"] = {}
        for t in report["themes_common"]:
            ap, bp = a_proms.get(t, 0), b_proms.get(t, 0)
            if ap != bp:
                report["theme_prominence_diffs"][t] = {"a": ap, "b": bp}

    # ── Characters ────────────────────────────────────────────────────────
    a_chars = {c["name"].lower() for c in a.get("characters", [])}
    b_chars = {c["name"].lower() for c in b.get("characters", [])}
    report["characters_added"] = sorted(b_chars - a_chars)
    report["characters_removed"] = sorted(a_chars - b_chars)
    report["characters_common"] = sorted(a_chars & b_chars)

    if detailed:
        a_imps = {c["name"].lower(): c["importance"] for c in a.get("characters", [])}
        b_imps = {c["name"].lower(): c["importance"] for c in b.get("characters", [])}
        report["character_importance_diffs"] = {}
        for c in report["characters_common"]:
            ai, bi = a_imps.get(c, 0), b_imps.get(c, 0)
            if ai != bi:
                report["character_importance_diffs"][c] = {"a": ai, "b": bi}

    # ── Categories ────────────────────────────────────────────────────────
    a_cats = set(c.lower() for c in a.get("categories", []))
    b_cats = set(c.lower() for c in b.get("categories", []))
    report["categories_match"] = a_cats == b_cats
    if not report["categories_match"]:
        report["categories_a"] = sorted(a_cats)
        report["categories_b"] = sorted(b_cats)

    # ── Content Flags ─────────────────────────────────────────────────────
    a_flags = set(a.get("content_flags", []))
    b_flags = set(b.get("content_flags", []))
    report["flags_added"] = sorted(b_flags - a_flags)
    report["flags_removed"] = sorted(a_flags - b_flags)

    # ── Ending ────────────────────────────────────────────────────────────
    report["ending_match"] = (
        a.get("sad_ending") == b.get("sad_ending") and
        a.get("cliffhanger") == b.get("cliffhanger")
    )

    # ── Similarity Score (0-100) ──────────────────────────────────────────
    score = 0
    max_score = 0

    # Metadata match: 10 points
    max_score += 10
    if report["metadata_match"]:
        score += 10

    # Rating similarity: 30 points (max 2 points per diff allowance per rating)
    max_score += 30
    rating_penalty = 0
    for rd in report["rating_diffs"].values():
        rating_penalty += min(abs(rd["diff"]), 3)  # Cap at 3 per rating
    score += max(0, 30 - rating_penalty * 2)

    # Theme overlap: 25 points
    max_score += 25
    all_themes = a_themes | b_themes
    if all_themes:
        overlap = len(a_themes & b_themes) / len(all_themes)
        score += round(overlap * 25)

    # Character overlap: 20 points
    max_score += 20
    all_chars = a_chars | b_chars
    if all_chars:
        overlap = len(a_chars & b_chars) / len(all_chars)
        score += round(overlap * 20)

    # Categories match: 5 points
    max_score += 5
    if report["categories_match"]:
        score += 5

    # Flags overlap: 5 points
    max_score += 5
    all_flags = a_flags | b_flags
    if all_flags:
        overlap = len(a_flags & b_flags) / len(all_flags)
        score += round(overlap * 5)
    else:
        score += 5

    # Ending match: 5 points
    max_score += 5
    if report["ending_match"]:
        score += 5

    report["total_score"] = round(score / max_score * 100) if max_score > 0 else 100

    return report


def print_report(report: dict, file_a: str, file_b: str):
    """Pretty-print the comparison report."""
    score = report["total_score"]

    # Color the score
    if score >= 90:
        grade = "EXCELLENT"
    elif score >= 75:
        grade = "GOOD"
    elif score >= 60:
        grade = "FAIR"
    else:
        grade = "POOR"

    print(f"\n{'═'*60}")
    print(f"  COMPARISON: {grade} — {score}/100 similarity")
    print(f"{'═'*60}")
    print(f"  A: {file_a}")
    print(f"  B: {file_b}")
    print(f"{'─'*60}")

    # Ratings
    if report["rating_diffs"]:
        print(f"\n  RATING CHANGES:")
        for key, rd in report["rating_diffs"].items():
            arrow = "↑" if rd["diff"] > 0 else "↓"
            print(f"    {key:15s}  {rd['a']} → {rd['b']}  ({arrow}{abs(rd['diff'])})")
    else:
        print(f"\n  RATINGS: identical ✓")

    # Themes
    if report["themes_removed"] or report["themes_added"]:
        print(f"\n  THEME CHANGES:")
        for t in report["themes_removed"]:
            print(f"    − {t}")
        for t in report["themes_added"]:
            print(f"    + {t}")
        print(f"    = {len(report['themes_common'])} themes unchanged")
    else:
        print(f"\n  THEMES: identical ({len(report['themes_common'])}) ✓")

    if "theme_prominence_diffs" in report and report["theme_prominence_diffs"]:
        print(f"\n  THEME PROMINENCE SHIFTS:")
        for t, d in report["theme_prominence_diffs"].items():
            print(f"    {t:30s}  {d['a']} → {d['b']}")

    # Characters
    if report["characters_removed"] or report["characters_added"]:
        print(f"\n  CHARACTER CHANGES:")
        for c in report["characters_removed"]:
            print(f"    − {c}")
        for c in report["characters_added"]:
            print(f"    + {c}")
        print(f"    = {len(report['characters_common'])} characters unchanged")
    else:
        print(f"\n  CHARACTERS: identical ({len(report['characters_common'])}) ✓")

    if "character_importance_diffs" in report and report["character_importance_diffs"]:
        print(f"\n  CHARACTER IMPORTANCE SHIFTS:")
        for c, d in report["character_importance_diffs"].items():
            print(f"    {c:20s}  {d['a']} → {d['b']}")

    # Categories
    if report["categories_match"]:
        print(f"\n  CATEGORIES: identical ✓")
    else:
        print(f"\n  CATEGORIES CHANGED:")
        print(f"    A: {', '.join(report.get('categories_a', []))}")
        print(f"    B: {', '.join(report.get('categories_b', []))}")

    # Flags
    if report["flags_removed"] or report["flags_added"]:
        print(f"\n  FLAG CHANGES:")
        for f in report["flags_removed"]:
            print(f"    − {f}")
        for f in report["flags_added"]:
            print(f"    + {f}")
    else:
        print(f"\n  FLAGS: identical ✓")

    # Ending
    if report["ending_match"]:
        print(f"\n  ENDING: identical ✓")
    else:
        print(f"\n  ENDING: CHANGED ✗")

    print(f"\n{'═'*60}\n")


def main():
    parser = argparse.ArgumentParser(
        description="Compare two book analysis JSON files"
    )
    parser.add_argument("file_a", help="First analysis JSON (baseline)")
    parser.add_argument("file_b", help="Second analysis JSON (test)")
    parser.add_argument("--detailed", "-d", action="store_true",
                       help="Show prominence and importance shifts")
    parser.add_argument("--json", action="store_true",
                       help="Output raw JSON instead of pretty print")

    args = parser.parse_args()

    a = load_json(args.file_a)
    b = load_json(args.file_b)

    report = compare(a, b, detailed=args.detailed)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_report(report, args.file_a, args.file_b)


if __name__ == "__main__":
    main()