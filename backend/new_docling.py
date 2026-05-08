"""
Generic PDF Chunker — handles ALL PDF types
============================================

Output per chunk matches the target format:
  {
    "index":           int,
    "page":            int,
    "tokens":          int,
    "heading_text":    "H1 > H2 > H3",
    "parent_heading":  "H1",
    "section_heading": "H3",
    "text":            "- item\\n\\nbody text..."
  }

Heading detection uses 4 layers in priority order:
  Layer 1: Docling SectionHeaderItem / TitleItem  (structured PDFs)
  Layer 2: x-coordinate tier clustering of LIST_ITEMs  (list-based / govt PDFs)
  Layer 3a: numbered section regex (1., 1.1., Section N:)  (technical manuals)
  Layer 3b: visual heuristics (ALL CAPS, Title Case)  (flat / scanned PDFs)

Frequently repeated short phrases (e.g. "About Scheme", "Benefits") are
detected as boilerplate sub-labels and EXCLUDED from heading detection —
they stay as content bullets.

Usage:
    python new_docling.py --pdf file.pdf
    python new_docling.py --pdf file.pdf --out output.json --max-tokens 300
"""

import json
import re
import argparse
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
from collections import Counter


# ──────────────────────────────────────────────────────────
# Item helpers
# ──────────────────────────────────────────────────────────

def _lbl(item) -> str:
    """Return the docling label as a plain string (handles both enum and str)."""
    l = getattr(item, "label", None)
    return l.value if hasattr(l, "value") else str(l) if l else "unknown"


def _page(item) -> Optional[int]:
    try:
        prov = getattr(item, "prov", None)
        if prov:
            p = prov[0] if isinstance(prov, list) else prov
            return getattr(p, "page_no", None)
    except Exception:
        pass
    return None


def _text(item) -> str:
    return (getattr(item, "text", None) or "").strip()


def _self_ref(item) -> str:
    return str(getattr(item, "self_ref", ""))


def _bbox_x(item) -> Optional[float]:
    """Return left x-coordinate from first provenance bbox, or None."""
    try:
        prov = getattr(item, "prov", None)
        if not prov:
            return None
        p = prov[0] if isinstance(prov, list) else prov
        bbox = getattr(p, "bbox", None)
        if bbox is None:
            return None
        if hasattr(bbox, "l"):
            return float(bbox.l)
        if hasattr(bbox, "x"):
            return float(bbox.x)
    except Exception:
        pass
    return None


# ──────────────────────────────────────────────────────────
# HeadingStack
# ──────────────────────────────────────────────────────────

class HeadingStack:
    """
    Maintains heading hierarchy across the document.
    New H(n) clears all entries at level ≥ n, then pushes H(n).
    """

    def __init__(self):
        self._stack: list[tuple[int, str]] = []

    def update(self, level: int, text: str):
        self._stack = [(l, t) for l, t in self._stack if l < level]
        self._stack.append((level, text))

    @property
    def path(self) -> list[str]:
        return [t for _, t in self._stack]

    def snapshot(self) -> list[str]:
        return list(self.path)

    def __repr__(self):
        return " > ".join(self.path) or "(empty)"


# ──────────────────────────────────────────────────────────
# RawItem
# ──────────────────────────────────────────────────────────

@dataclass
class RawItem:
    text: str
    page: int
    label: str
    is_heading: bool
    level: int            # 0 if not a heading
    heading_path: list[str] = field(default_factory=list)


@dataclass
class DocumentProfile:
    x_tiers: list[float]
    xcoord_refs: set[str]
    total_list_items: int
    xcoord_candidate_ratio: float
    allow_xcoord_list_headings: bool


# ──────────────────────────────────────────────────────────
# Pre-pass: detect boilerplate sub-labels (exclusion filter)
# ──────────────────────────────────────────────────────────

def detect_structural_repeats(all_texts: list) -> set:
    """
    Find short phrases that repeat ≥ 4 times.  These are boilerplate sub-labels
    ("About Scheme", "Benefits", "Who Can Apply") that must stay as content
    bullets and never be promoted to headings.

    Returns a set of normalized lowercase strings.
    """
    _BULLETS = "·•‣◦⁃-* \t"

    def _normalise(t: str) -> Optional[str]:
        t = t.strip().lstrip(_BULLETS).strip()
        if not t or len(t) > 60:
            return None
        if t[-1] in ".,;:":
            return None
        words = t.split()
        if not words or len(words) > 6:
            return None
        if not any(c.isalpha() for c in t):
            return None
        return " ".join(t.split()).lower()

    normalised = [_normalise(t) for t in all_texts]
    freq = Counter(n for n in normalised if n)
    min_count = max(4, int(len(normalised) * 0.005))
    return {phrase for phrase, count in freq.items() if count >= min_count}


# ──────────────────────────────────────────────────────────
# Heading text helpers
# ──────────────────────────────────────────────────────────

_BULLET_CHARS = "·•‣◦⁃-* \t"
_XCOORD_PROMOTION_RATIO_LIMIT = 0.20
_FORM_LABELS = {
    "name", "address", "mobile", "mobile number", "phone", "phone number",
    "email", "email id", "e-mail", "date", "signature", "district",
    "state", "pin", "pincode", "pin code", "gender", "age", "dob",
    "father name", "mother name", "applicant name", "account number",
    "ifsc", "bank", "bank name", "category", "status",
}
_CONTEXTUAL_COLON_PREFIXES = {
    "purpose", "features", "key points", "login types", "common issues",
    "common issues covered", "scheme types", "dashboard displays",
    "eligibility factors may include", "documents may include",
    "application status", "submission", "grievance status", "includes",
    "common technical issues", "handled scenarios", "fallback",
    "about department", "about ramp", "objective", "scheme applicable for",
    "detailed information", "how to apply", "general steps",
}
_PROCEDURAL_CONTEXT_WORDS = {
    "step", "steps", "process", "procedure", "how to apply", "general steps",
    "application process", "instructions",
}


def _heading_text(t: str) -> str:
    """Strip leading bullet chars so '· Steps for Scheme' → 'Steps for Scheme'."""
    return t.strip().lstrip(_BULLET_CHARS).strip()


def _norm_text(t: str) -> str:
    return " ".join(_heading_text(t).split()).lower()


def _label_core(text: str) -> str:
    return _norm_text(text).rstrip(":").strip()


def is_probable_form_label(text: str) -> bool:
    t = _heading_text(text).strip()
    if not t.endswith(":"):
        return False
    core = _label_core(t)
    return core in _FORM_LABELS


def is_contextual_colon_heading(text: str) -> bool:
    t = _heading_text(text).strip()
    if not t.endswith(":") or is_probable_form_label(t):
        return False
    core = _label_core(t)
    if core in _CONTEXTUAL_COLON_PREFIXES:
        return True
    words = core.split()
    return 1 <= len(words) <= 5 and any(c.isalpha() for c in core)


def is_boilerplate_section_label(text: str) -> bool:
    core = _label_core(text)
    return (
        core.startswith("implemented by")
        or core.startswith("implimented by")
        or core.startswith("under department")
    )


def _inside_procedural_context(stack: HeadingStack) -> bool:
    for heading in stack.path[-2:]:
        h = _label_core(heading)
        if h in _PROCEDURAL_CONTEXT_WORDS:
            return True
        if any(word in h for word in _PROCEDURAL_CONTEXT_WORDS):
            return True
    return False


def _nearest_numbered_child_level(stack: HeadingStack) -> Optional[int]:
    for level, heading in reversed(stack._stack):
        if is_strong_numbered_heading(heading):
            return level + 1
    return None


def _contextual_colon_level(stack: HeadingStack) -> Optional[int]:
    if stack._stack and is_contextual_colon_heading(stack._stack[-1][1]):
        return stack._stack[-1][0]
    return _nearest_numbered_child_level(stack)


# ──────────────────────────────────────────────────────────
# Layer 2: x-coordinate tier detection
# ──────────────────────────────────────────────────────────

# Only the first N tiers of list_items are heading candidates.
# Deeper tiers are content (sub-bullets, detail items).
_MAX_LIST_HEADING_TIERS = 2


def build_xcoord_structure(
    all_items: list,
    structural_repeats: set,
) -> DocumentProfile:
    """
    Build x-coordinate tier list from non-structural list_items.

    Returns a DocumentProfile with x tiers, list-item heading candidates, and
    a safety gate for noisy straight-left documents.

    If no meaningful indentation exists, x_tiers and xcoord_refs are empty.
    """
    raw = []
    total_list_items = 0
    for item, _ in all_items:
        if _lbl(item) != "list_item":
            continue
        total_list_items += 1
        t = _text(item)
        if not t or len(t) > 120 or len(t.split()) > 10:
            continue
        norm = " ".join(t.strip().split()).lower()
        if norm in structural_repeats:
            continue
        x = _bbox_x(item)
        if x is not None:
            raw.append((_self_ref(item), x))

    if not raw:
        return DocumentProfile([], set(), total_list_items, 0.0, False)

    xs = [x for _, x in raw]
    if max(xs) - min(xs) < 15:
        return DocumentProfile([], set(), total_list_items, 0.0, False)

    tiers: list[float] = []
    for x in sorted(set(xs)):
        if not tiers or x - tiers[-1] > 5.0:
            tiers.append(x)

    # Only include list_items from the first _MAX_LIST_HEADING_TIERS tiers.
    # Items at deeper tiers are content (sub-bullets, detail items), not headings.
    candidate_refs = {
        ref for ref, x in raw
        if _xcoord_tier(x, tiers) < _MAX_LIST_HEADING_TIERS
    }
    ratio = len(candidate_refs) / total_list_items if total_list_items else 0.0
    allow = bool(tiers) and ratio < _XCOORD_PROMOTION_RATIO_LIMIT
    return DocumentProfile(tiers, candidate_refs, total_list_items, ratio, allow)


def _xcoord_tier(x: Optional[float], tiers: list) -> int:
    """
    Return 0-based tier index for x-coordinate.
    Returns len(tiers) if x is beyond all known tiers.
    """
    if x is None or not tiers:
        return 0
    if x <= tiers[0] + 5.0:
        return 0
    for i, t in enumerate(tiers):
        if abs(x - t) <= 5.0:
            return i
    return len(tiers)


# ──────────────────────────────────────────────────────────
# Layer 3a: numbered section headings
# ──────────────────────────────────────────────────────────

_NUMBERED_RE = re.compile(r"^(\d+(?:\.\d+)*)(?:[.)])?\s+\S")
_SECTION_RE = re.compile(r"^(section|chapter|part)\s+\d+", re.I)


def is_strong_numbered_heading(text: str) -> bool:
    t = text.strip()
    # Numbered list items end with sentence-ending punctuation; headings don't.
    if t and t[-1] in ".,;":
        return False
    # Full provisions start with a number but are long sentences — not headings.
    if len(t) > 120:
        return False
    return bool(_NUMBERED_RE.match(t) or _SECTION_RE.match(t))


def _is_numbered_heading(text: str) -> bool:
    return is_strong_numbered_heading(text)


def _numbered_level(text: str) -> int:
    m = _NUMBERED_RE.match(text)
    if m:
        return max(1, m.group(1).count(".") + 1)
    return 1


# ──────────────────────────────────────────────────────────
# Layer 3b: visual text heuristics (last resort)
# ──────────────────────────────────────────────────────────

_STOP_WORDS = {
    "in", "of", "the", "for", "and", "to", "a", "an",
    "at", "by", "on", "or", "from", "with", "into",
}


def _content_word_cap_ratio(words: list) -> float:
    content = [w for w in words if w.lower() not in _STOP_WORDS and w.isalpha()]
    if not content:
        return sum(1 for w in words if w and w[0].isupper()) / len(words) if words else 0.0
    return sum(1 for w in content if w and w[0].isupper()) / len(content)


def _visual_heading_level(text: str) -> int:
    """
    Return the heading level (1 or 2) if text looks like a heading, else 0.
    Strict conditions to minimise false positives.
    """
    t = text.strip()
    if not t:
        return 0

    # URLs and hyperlink references are never headings
    if t.startswith('"http') or t.startswith('http') or "HYPERLINK" in t:
        return 0

    words = t.split()

    if len(t) > 120 or len(words) > 15:
        return 0
    if t[-1] in ".,;":
        return 0
    if t[0] in "·•‣◦⁃-*":
        return 0
    if words[0].rstrip(".").isdigit():
        return 0

    # Clarifying-paren filter: parens with any lowercase letter → body text
    if "(" in t and ")" in t:
        ps = t.find("(")
        pe = t.find(")", ps)
        if pe > ps and any(c.islower() for c in t[ps + 1:pe]):
            return 0

    alpha = [c for c in t if c.isalpha()]
    if not alpha:
        return 0

    is_all_caps = len(alpha) > 3 and all(c.isupper() for c in alpha)
    cap_ratio = _content_word_cap_ratio(words)
    is_title_case = cap_ratio >= 0.85 and 2 <= len(words) <= 8

    if is_all_caps and len(words) >= 2:
        return 1
    if is_title_case:
        return 2
    return 0


def should_promote_list_item_by_xcoord(ref: str, profile: DocumentProfile) -> bool:
    return profile.allow_xcoord_list_headings and ref in profile.xcoord_refs


def resolve_heading_level(
    item,
    signal: str,
    stack: HeadingStack,
    profile: DocumentProfile,
) -> int:
    text = _text(item)
    x = _bbox_x(item)

    if signal == "title":
        return 1
    if signal == "numbered":
        return _numbered_level(text)
    if signal == "contextual_colon":
        return 2 if stack.path else 1
    if signal == "xcoord_list":
        tier = _xcoord_tier(x, profile.x_tiers)
        return tier + 2
    if signal == "section_header":
        if profile.x_tiers:
            tier = _xcoord_tier(x, profile.x_tiers)
            if tier == 0:
                return 1
            if tier >= len(profile.x_tiers) and stack.path:
                return tier + 2
            if stack.path:
                return min(tier + 2, max(l for l, _ in stack._stack) + 1)
            return 2
        docling_lv = getattr(item, "level", None)
        return max(1, int(docling_lv)) if docling_lv is not None else 2
    if signal == "visual":
        return _visual_heading_level(text)
    return 0


# ──────────────────────────────────────────────────────────
# collect_raw_items — unified 4-layer detection
# ──────────────────────────────────────────────────────────

def collect_raw_items(doc) -> list:
    all_items = list(doc.iterate_items())
    all_texts = [_text(item) for item, _ in all_items if _text(item)]

    structural_repeats = detect_structural_repeats(all_texts)
    profile = build_xcoord_structure(all_items, structural_repeats)
    collect_raw_items.last_profile = profile
    has_xcoord = bool(profile.x_tiers)

    stack = HeadingStack()
    raw: list[RawItem] = []

    for item, _ in all_items:
        t = _text(item)
        if not t:
            continue

        pg = _page(item) or (raw[-1].page if raw else 1)
        lbl = _lbl(item)
        ref = _self_ref(item)
        t_norm = _norm_text(t)

        is_heading = False
        level = 0

        if has_xcoord:
            if lbl == "title":
                is_heading = True
                level = 1
            elif (
                lbl == "section_header"
                and not is_probable_form_label(t)
                and not is_boilerplate_section_label(t)
            ):
                tier = _xcoord_tier(_bbox_x(item), profile.x_tiers)
                is_heading = True
                contextual_level = _contextual_colon_level(stack)
                if is_contextual_colon_heading(t) and contextual_level is not None:
                    level = contextual_level
                elif is_contextual_colon_heading(t) and stack.path and tier != 0:
                    level = max(l for l, _ in stack._stack) + 1
                else:
                    level = 1 if tier == 0 else tier + 2
            elif lbl == "list_item" and should_promote_list_item_by_xcoord(ref, profile):
                tier = _xcoord_tier(_bbox_x(item), profile.x_tiers)
                is_heading = True
                level = tier + 2
        else:
            if lbl == "title":
                is_heading = True
                level = 1
            elif (
                lbl == "section_header"
                and not is_probable_form_label(t)
                and not is_boilerplate_section_label(t)
            ):
                is_heading = True
                contextual_level = _contextual_colon_level(stack)
                if is_contextual_colon_heading(t) and contextual_level is not None:
                    level = contextual_level
                elif is_contextual_colon_heading(t) and stack.path:
                    level = max(l for l, _ in stack._stack) + 1
                else:
                    docling_lv = getattr(item, "level", None)
                    level = max(1, int(docling_lv)) if docling_lv is not None else 2

        _3a_eligible = lbl in ("text", "paragraph") or (not has_xcoord and lbl == "list_item")
        if not is_heading and _3a_eligible:
            if (
                t_norm not in structural_repeats
                and is_strong_numbered_heading(t)
                and not (lbl == "list_item" and _inside_procedural_context(stack))
            ):
                is_heading = True
                level = _numbered_level(t)

        if has_xcoord:
            eligible_for_visual = (
                lbl in ("text", "paragraph")
                and _xcoord_tier(_bbox_x(item), profile.x_tiers) < _MAX_LIST_HEADING_TIERS
            )
        else:
            eligible_for_visual = lbl in ("text", "paragraph")
        if not is_heading and eligible_for_visual:
            if t_norm not in structural_repeats and not is_probable_form_label(t):
                lv = _visual_heading_level(t)
                if lv:
                    is_heading = True
                    level = lv

        if is_heading:
            stack.update(level, _heading_text(t))

        raw.append(RawItem(
            text=t,
            page=pg,
            label=lbl,
            is_heading=is_heading,
            level=level,
            heading_path=stack.snapshot(),
        ))

    return raw


# ──────────────────────────────────────────────────────────
# Content serialization + token helpers
# ──────────────────────────────────────────────────────────

def serialize_items(items: list) -> str:
    parts = []
    for item in items:
        if item.label == "list_item":
            parts.append("- " + item.text)
        else:
            parts.append(item.text)
    return "\n\n".join(p for p in parts if p).strip()


def count_tokens(text: str) -> int:
    return max(1, round(len(text.split()) * 1.35))


def split_by_tokens(text: str, max_tokens: int) -> list:
    """Split at paragraph boundaries to stay within max_tokens."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        return []
    segments = []
    current: list = []
    current_tokens = 0
    for para in paragraphs:
        pt = count_tokens(para)
        if current and current_tokens + pt > max_tokens:
            segments.append("\n\n".join(current))
            current = [para]
            current_tokens = pt
        else:
            current.append(para)
            current_tokens += pt
    if current:
        segments.append("\n\n".join(current))
    return segments


# ──────────────────────────────────────────────────────────
# Build target chunks
# ──────────────────────────────────────────────────────────

_MIN_CHUNK_TOKENS = 40


def _is_junk_text(text: str) -> bool:
    """True for garbled/encoded text (e.g. Hindi read as mojibake) or whitespace-only."""
    if not text or not text.strip():
        return True
    non_ascii = sum(1 for c in text if ord(c) > 127)
    return non_ascii / len(text) > 0.30


def build_target_chunks(raw: list, max_tokens: int = 300) -> list:
    chunks = []
    counter = 0

    def _flush(heading_path: list, content_items: list):
        nonlocal counter
        text = serialize_items(content_items)
        if not text:
            return
        page = next(
            (it.page for it in content_items),
            content_items[0].page if content_items else 1,
        )
        for seg in split_by_tokens(text, max_tokens):
            if not seg.strip():
                continue
            if _is_junk_text(seg):
                continue
            if count_tokens(seg) < _MIN_CHUNK_TOKENS:
                continue
            chunks.append({
                "index":           counter,
                "page":            page,
                "tokens":          count_tokens(seg),
                "heading_text":    " > ".join(heading_path),
                "parent_heading":  heading_path[0] if heading_path else "",
                "section_heading": heading_path[-1] if heading_path else "",
                "text":            seg,
            })
            counter += 1

    current_path: list = []
    content_buffer: list = []

    for item in raw:
        # Skip content that appears before any heading is encountered (e.g. TOC)
        if not item.heading_path:
            continue

        path_changed = tuple(item.heading_path) != tuple(current_path)

        if item.is_heading:
            if path_changed:
                if content_buffer:
                    _flush(current_path, content_buffer)
                    content_buffer = []
                current_path = item.heading_path
        else:
            if path_changed:
                if content_buffer:
                    _flush(current_path, content_buffer)
                    content_buffer = []
                current_path = item.heading_path
            content_buffer.append(item)

    if content_buffer:
        _flush(current_path, content_buffer)

    return chunks


# ──────────────────────────────────────────────────────────
# Page-based fallback (no headings detected)
# ──────────────────────────────────────────────────────────

def build_page_chunks(raw: list, max_tokens: int = 300) -> list:
    from collections import defaultdict
    page_map: dict = defaultdict(list)
    for item in raw:
        if not item.is_heading:
            page_map[item.page].append(item)

    chunks = []
    counter = 0
    for pg in sorted(page_map):
        text = serialize_items(page_map[pg])
        if not text:
            continue
        for seg in split_by_tokens(text, max_tokens):
            if not seg.strip():
                continue
            if _is_junk_text(seg) or count_tokens(seg) < _MIN_CHUNK_TOKENS:
                continue
            chunks.append({
                "index":           counter,
                "page":            pg,
                "tokens":          count_tokens(seg),
                "heading_text":    "",
                "parent_heading":  "",
                "section_heading": "",
                "text":            seg,
            })
            counter += 1
    return chunks


# ──────────────────────────────────────────────────────────
# Load PDF
# ──────────────────────────────────────────────────────────

def load_doc(pdf_path: str):
    from docling.document_converter import DocumentConverter
    converter = DocumentConverter()
    return converter.convert(source=pdf_path).document


# ──────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────

def run(pdf_path: str, out_path: str, max_tokens: int = 300):
    source = Path(pdf_path).name

    print(f"\n[1/4] Loading: {pdf_path}")
    doc = load_doc(pdf_path)

    print("[2/4] Collecting items...")
    raw = collect_raw_items(doc)
    heading_count = sum(1 for r in raw if r.is_heading)
    print(f"  Items: {len(raw)} | Headings: {heading_count} | Content: {len(raw) - heading_count}")
    profile = getattr(collect_raw_items, "last_profile", None)
    if profile is not None:
        print(
            "  X-profile: "
            f"list_items={profile.total_list_items} | "
            f"x_candidates={len(profile.xcoord_refs)} | "
            f"ratio={profile.xcoord_candidate_ratio:.1%} | "
            f"x_list_headings={'on' if profile.allow_xcoord_list_headings else 'off'}"
        )

    print("[3/4] Building chunks...")
    if heading_count == 0:
        print("  No headings found — using page-based fallback")
        chunks = build_page_chunks(raw, max_tokens)
    else:
        chunks = build_target_chunks(raw, max_tokens)

    print(f"[4/4] Writing {len(chunks)} chunks -> {out_path}")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(chunks, f, indent=2, ensure_ascii=False)

    print(f"\nDone -> {out_path}\n")

    # Preview
    print("=" * 65)
    for c in chunks[:8]:
        path = c["heading_text"] or "(no heading)"
        snippet = c["text"][:90].replace("\n", " ")
        try:
            print(f"\n  [p{c['page']}] {path}")
            print(f"     tokens : {c['tokens']}")
            print(f"     text   : {snippet}...")
        except UnicodeEncodeError:
            safe = snippet.encode("ascii", "replace").decode()
            print(f"\n  [p{c['page']}] {path}")
            print(f"     tokens : {c['tokens']}")
            print(f"     text   : {safe}...")
    print("=" * 65)


# ──────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generic PDF chunker — all PDF types")
    parser.add_argument("--pdf", required=True, help="Input PDF path")
    parser.add_argument("--out", default="chunks.json", help="Output JSON path")
    parser.add_argument(
        "--max-tokens", type=int, default=300,
        help="Max tokens per chunk before splitting (default 300)",
    )
    args = parser.parse_args()

    run(pdf_path=args.pdf, out_path=args.out, max_tokens=args.max_tokens)
