#!/usr/bin/env python3
"""Build a Latin-only variant of Dongle with rebalanced metrics.

Run:
    python build_latin.py

Output:
    fonts/ttf/DongleLatin-Light.ttf
    fonts/ttf/DongleLatin-Regular.ttf
    fonts/ttf/DongleLatin-Bold.ttf

What this does, in order:
  1. Extracts sources/Dongle.zip -> sources/Dongle.glyphs (if not already done).
  2. Loads the .glyphs source via glyphsLib.
  3. Deletes glyphs whose Unicode falls in CJK / Hangul ranges, and glyphs
     whose names match Korean-only patterns (.full fullwidth variants, *-ko).
  4. Strips references to deleted glyphs out of OT class/feature code so the
     build doesn't trip on dangling names.
  5. Rebalances vertical metrics: typo/hhea/win ascender = 900, descender = -300.
     Line height becomes ~1.2x em (comfortable Latin leading) instead of ~1.45x.
  6. Renames family to "Dongle Latin" (OFL Reserved Font Name requires a new
     name for modified versions).
  7. Saves sources/DongleLatin.glyphs, converts to UFO, compiles TTFs.

Iteration notes:
  - Tune NEW_ASCENDER / NEW_DESCENDER below to taste.
  - If the build complains about missing glyphs in feature code, look at
    strip_removed_refs() -- the regex cleanup is conservative.
  - The DROP_RANGES / KOREAN_NAME_PATTERNS lists drive what gets removed.
"""

from __future__ import annotations

import multiprocessing
import multiprocessing.pool
import re
import shutil
import zipfile
from pathlib import Path

import glyphsLib
import ufo2ft
import ufoLib2
from fontTools.ttLib import TTFont, newTable
from glyphsLib.cli import main as glyphs_cli


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SRC_DIR = Path("sources")
SRC_ZIP = SRC_DIR / "Dongle.zip"
SRC_GLYPHS = SRC_DIR / "Dongle.glyphs"
OUT_GLYPHS = SRC_DIR / "DongleLatin.glyphs"
OUT_DIR = Path("fonts/ttf")

NEW_FAMILY_NAME = "Dongle Latin"

# Comfortable Latin line metrics (em = 1000).
# Cap top sits at ~400, descenders bottom at ~-150.
# ascender 900 leaves ~500 above caps (room for stacked diacritics + leading).
# descender -300 leaves ~150 below descenders (typical Latin breathing room).
# Total line = 1200 units = 1.2x em -> book-body leading.
NEW_ASCENDER = 750   # tallest real glyph is Vietnamese Ắ at y=735 (Bold)
NEW_DESCENDER = -300  # signed; winDescent will be stored as its absolute value

# Drop glyphs whose Unicode falls in any of these ranges.
DROP_RANGES = [
    (0x1100, 0x11FF),   # Hangul Jamo
    (0x3000, 0x303F),   # CJK Symbols and Punctuation
    (0x3130, 0x318F),   # Hangul Compatibility Jamo
    (0x3200, 0x33FF),   # Enclosed CJK / CJK Compatibility
    (0x4E00, 0x9FFF),   # CJK Unified Ideographs
    (0xA960, 0xA97F),   # Hangul Jamo Extended-A
    (0xAC00, 0xD7FF),   # Hangul Syllables + Jamo Extended-B
    (0xF900, 0xFAFF),   # CJK Compat Ideographs
    (0xFE30, 0xFE4F),   # CJK Compat Forms
    (0xFF00, 0xFFEF),   # Halfwidth & Fullwidth Forms (catches A.full etc.)
    # Box drawing and block elements: full-cell glyphs designed to tile
    # vertically, so they extend to y=-460 / +540 and inflate the font's
    # bounding box (which some apps use for line height) even though we
    # tightened the typo metrics.
    (0x2500, 0x257F),   # Box Drawing
    (0x2580, 0x259F),   # Block Elements
]

# Drop glyphs whose name matches any of these regexes (Korean-only variants
# with no Unicode mapping won't be caught by DROP_RANGES alone).
KOREAN_NAME_PATTERNS = [
    re.compile(r"\.full$"),                 # A.full, B.full, ...
    re.compile(r"-ko$"),                    # *-ko (Glyphs Korean naming)
    re.compile(r"hangul", re.IGNORECASE),
    re.compile(r"^uni(11|31[3-8]|A96|AC|AD|AE|AF|B[0-9A-F]|C[0-9A-F]|D[0-7])",
               re.IGNORECASE),               # Hangul-range uniXXXX names
]


# ---------------------------------------------------------------------------
# Source extraction
# ---------------------------------------------------------------------------

def extract_source():
    if SRC_GLYPHS.exists():
        return
    print(f"[Dongle Latin] Extracting {SRC_ZIP}")
    with zipfile.ZipFile(SRC_ZIP) as z:
        z.extract("Dongle.glyphs", path=SRC_DIR)


# ---------------------------------------------------------------------------
# Glyph filtering
# ---------------------------------------------------------------------------

def get_codepoints(glyph):
    """Return integer codepoints for a glyph (possibly empty)."""
    u = getattr(glyph, "unicode", None)
    if u is None:
        return []
    if isinstance(u, (list, tuple)):
        items = u
    else:
        items = [u]
    out = []
    for s in items:
        if s is None:
            continue
        if isinstance(s, int):
            out.append(s)
            continue
        try:
            out.append(int(s, 16))
        except (TypeError, ValueError):
            pass
    return out


def in_drop_range(cp):
    return any(lo <= cp <= hi for lo, hi in DROP_RANGES)


def matches_korean_name(name):
    return any(p.search(name) for p in KOREAN_NAME_PATTERNS)


def should_drop(glyph):
    cps = get_codepoints(glyph)
    if cps and any(in_drop_range(cp) for cp in cps):
        return True
    if matches_korean_name(glyph.name):
        return True
    return False


def filter_glyphs(font):
    """Remove CJK / Hangul / fullwidth glyphs. Returns (original_names, kept_names)."""
    original_names = {g.name for g in font.glyphs}

    drop = {g.name for g in font.glyphs if should_drop(g)}

    # Don't drop glyphs that kept glyphs depend on as components.
    by_name = {g.name: g for g in font.glyphs}
    kept = original_names - drop

    # Transitive closure: pull components of kept glyphs back into kept,
    # even if they matched a drop rule. (Unlikely for CJK->Latin but safe.)
    grew = True
    while grew:
        grew = False
        for name in list(kept):
            g = by_name.get(name)
            if g is None:
                continue
            for layer in g.layers:
                for comp in (getattr(layer, "components", None) or []):
                    cname = getattr(comp, "name", None) \
                        or getattr(comp, "componentName", None)
                    if cname and cname in drop:
                        drop.discard(cname)
                        kept.add(cname)
                        grew = True

    # Apply deletion. glyphsLib's font.glyphs supports `del` by index.
    for i in range(len(font.glyphs) - 1, -1, -1):
        if font.glyphs[i].name in drop:
            del font.glyphs[i]

    print(f"[Dongle Latin] Glyphs: {len(original_names)} -> {len(kept)} "
          f"(dropped {len(drop)})")
    return original_names, kept


# ---------------------------------------------------------------------------
# Class / feature reference cleanup
# ---------------------------------------------------------------------------

# Match glyph-name-shaped tokens. FEA keywords like 'sub', 'by', 'lookup',
# 'feature', 'script', 'language' won't be in our removed-names set, so they
# pass through untouched.
_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9._]*")

# OpenType features that exist only for CJK layout. Drop them wholesale rather
# than try to repair their substitution rules after Korean glyphs disappear.
CJK_ONLY_FEATURES = {"fwid", "hwid", "halt", "palt", "vert", "vrt2", "vkrn",
                     "vpal", "valt", "vhal", "ruby", "ljmo", "vjmo", "tjmo"}


def _clean_code(code, removed):
    if not code:
        return code

    def repl(m):
        tok = m.group(0)
        return "" if tok in removed else tok

    out = _NAME_RE.sub(repl, code)
    out = re.sub(r"[ \t]+", " ", out)
    out = re.sub(r"\[\s*\]", "[]", out)
    out = re.sub(r",\s*,+", ",", out)

    # Drop substitution rules with an empty side (left over from name stripping):
    #   sub X by ;        sub  by Y;       sub X by [];
    out = re.sub(r"\bsub\s*\bby\b[^;]*;\s*", "", out)
    out = re.sub(r"\bsub\b[^;]*\bby\s*;\s*", "", out)
    out = re.sub(r"\bsub\b[^;]*\bby\s*\[\s*\]\s*;\s*", "", out)
    return out


def strip_removed_refs(font, original_names, kept_names):
    removed = original_names - kept_names

    # Drop CJK-only features wholesale. They reference removed glyphs and have
    # no meaning in a Latin-only build.
    if font.features:
        before = len(font.features)
        font.features = [f for f in font.features
                         if getattr(f, "name", None) not in CJK_ONLY_FEATURES]
        dropped = before - len(font.features)
        if dropped:
            print(f"[Dongle Latin] Dropped {dropped} CJK-only features")

    if not removed:
        return

    for cls in (font.classes or []):
        cls.code = _clean_code(cls.code, removed)
    for fp in (font.featurePrefixes or []):
        fp.code = _clean_code(fp.code, removed)
    for feat in (font.features or []):
        feat.code = _clean_code(feat.code, removed)


# ---------------------------------------------------------------------------
# Metrics + family name
# ---------------------------------------------------------------------------

def update_metrics(font):
    for m in font.masters:
        m.ascender = NEW_ASCENDER
        m.descender = NEW_DESCENDER

        # winDescent is stored as positive in the OS/2 table.
        params = {
            "winAscent": NEW_ASCENDER,
            "winDescent": abs(NEW_DESCENDER),
            "hheaAscender": NEW_ASCENDER,
            "hheaDescender": NEW_DESCENDER,
            "hheaLineGap": 0,
            "typoLineGap": 0,
        }
        for name, value in params.items():
            m.customParameters[name] = value


def update_family(font):
    font.familyName = NEW_FAMILY_NAME


def clean_ufo_duplicate_unicodes(ufo_path):
    """Drop duplicate Unicode mappings inside a UFO. ufo2ft refuses to compile
    a UFO where two glyphs claim the same codepoint; glyphsLib can produce
    these via name-database lookup (e.g. dblverticalbar gets U+2225 even when
    parallel already has it). Keep whichever glyph the UFO encountered first."""
    font = ufoLib2.Font.open(ufo_path)
    seen = {}
    for glyph in font:
        if not glyph.unicodes:
            continue
        kept = []
        for cp in glyph.unicodes:
            owner = seen.get(cp)
            if owner is None:
                seen[cp] = glyph.name
                kept.append(cp)
            else:
                print(f"[Dongle Latin] {ufo_path.name}: U+{cp:04X} kept on "
                      f"{owner!r}, removed from {glyph.name!r}")
        glyph.unicodes = kept
    font.save()


# ---------------------------------------------------------------------------
# Build pipeline (mirrors build.py)
# ---------------------------------------------------------------------------

def DSIG_modification(font: TTFont):
    font["DSIG"] = newTable("DSIG")
    font["DSIG"].ulVersion = 1
    font["DSIG"].usFlag = 0
    font["DSIG"].usNumSigs = 0
    font["DSIG"].signatureRecords = []


def GASP_set(font: TTFont):
    if "gasp" not in font:
        font["gasp"] = newTable("gasp")
        font["gasp"].gaspRange = {}
    if font["gasp"].gaspRange != {65535: 0x000A}:
        font["gasp"].gaspRange = {65535: 0x000A}


def build_ufo(ufo_path: Path):
    source = ufoLib2.Font.open(ufo_path)
    source.lib["com.github.googlei18n.ufo2ft.filters"] = [
        {"name": "decomposeTransformedComponents", "pre": 1},
        {"name": "flattenComponents", "pre": 1},
    ]
    style = source.info.styleName
    family = source.info.familyName

    ttf = ufo2ft.compileTTF(
        source,
        removeOverlaps=True,
        overlapsBackend="pathops",
        useProductionNames=True,
    )
    DSIG_modification(ttf)
    GASP_set(ttf)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{family.replace(' ', '')}-{str(style).replace(' ', '')}.ttf"
    print(f"[Dongle Latin {style}] Writing {out}")
    ttf.save(out)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    extract_source()

    print("[Dongle Latin] Loading source")
    font = glyphsLib.GSFont(str(SRC_GLYPHS))

    original_names, kept_names = filter_glyphs(font)
    strip_removed_refs(font, original_names, kept_names)
    update_metrics(font)
    update_family(font)

    print(f"[Dongle Latin] Saving {OUT_GLYPHS}")
    font.save(str(OUT_GLYPHS))

    print("[Dongle Latin] Converting to UFO")
    glyphs_cli((
        "glyphs2ufo",
        str(OUT_GLYPHS),
        "--write-public-skip-export-glyphs",
        "--propagate-anchors",
    ))

    ufos = sorted(SRC_DIR.glob("DongleLatin*.ufo"))
    if not ufos:
        raise SystemExit("No DongleLatin*.ufo produced; aborting build.")

    for u in ufos:
        clean_ufo_duplicate_unicodes(u)

    pool = multiprocessing.pool.Pool(processes=multiprocessing.cpu_count())
    procs = [pool.apply_async(build_ufo, (u,)) for u in ufos]
    pool.close()
    pool.join()
    for p in procs:
        p.get()

    for u in ufos:
        shutil.rmtree(u)
    ds = SRC_DIR / "DongleLatin.designspace"
    if ds.exists():
        ds.unlink()

    print("[Dongle Latin] Done.")


if __name__ == "__main__":
    main()
