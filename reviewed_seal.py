"""Compile reviewed historical Kaiyuan outlines as data, without executing upstream code."""

import csv
import hashlib
import io
import tarfile
from xml.etree import ElementTree

REV = "97433695858330ec9fdb7de9c4604da2f635eba6"
URL = f"https://codeload.github.com/frankslin/kaiyuan-small-seal-font/tar.gz/{REV}"
SHA = "6249a03976bb0f3bc2fb5d13185d909b3444c1f90b8a7d56dfe8629d1d56577a"
PREFIX = f"kaiyuan-small-seal-font-{REV}/"
FONT = "FontRoutesReviewedSeal-Regular.ttf"
PS = "FontRoutesReviewedSeal-Regular"
ATTRIBUTION = "Copyright (c) 2026 Kaiyuan Small Seal Font Contributors"
FAMILY = "FontRoutes Reviewed Seal"
FIRST, LAST = 0x3D000, 0x3FC3F
EXPECTED = 1197
NOTICE_PATHS = {
    "Kaiyuan-OFL.txt": "OFL.txt",
    "Kaiyuan-THIRD-PARTY-NOTICES.md": "THIRD_PARTY_NOTICES.md",
    "Kaiyuan-Unicode-Data-License.txt": "third_party/unicode/ucd/LICENSE.txt",
}


def require(flag, message):
    if not flag:
        raise ValueError(message)


def make_font(raw):
    """Return deterministic static font + notices/provenance, preserving approved-only semantics."""
    from fontTools import __version__ as fonttools_version
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.boundsPen import BoundsPen
    from fontTools.pens.cu2quPen import Cu2QuPen
    from fontTools.pens.recordingPen import RecordingPen
    from fontTools.pens.transformPen import TransformPen
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    from fontTools.svgLib.path import parse_path
    from fontTools.ttLib import TTFont

    require(hashlib.sha256(raw).hexdigest() == SHA, "Kaiyuan archive digest mismatch")
    require(fonttools_version == "4.61.1", "Reviewed Seal requires fontTools 4.61.1")
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as source:
        files = {}
        for item in source:
            if item.isfile() and item.name.startswith(PREFIX):
                path = item.name[len(PREFIX):]
                require(path not in files, "Duplicate Kaiyuan archive member")
                files[path] = item

        def read(path):
            obj = files.get(path)
            require(obj is not None and 0 < obj.size <= 512_000, "Missing or oversized Kaiyuan member: " + path)
            with source.extractfile(obj) as stream:
                return stream.read()

        approval = read("data/approved.csv")
        approved = {}
        for item in csv.DictReader(io.StringIO(approval.decode("utf-8"))):
            cp = int(item["codepoint"], 16)
            require(FIRST <= cp <= LAST and cp not in approved and item["status"] == "approved" and
                    all(item.get(k) for k in ("commons_title", "edition", "page")),
                    "Unreviewed, duplicate or unprovenanced Kaiyuan glyph")
            approved[cp] = f"u{cp:05X}"
        require(len(approved) == EXPECTED, "Unexpected Kaiyuan human-approved target count")
        glyphs = {".notdef": TTGlyphPen(None).glyph()}
        for cp, name in sorted(approved.items()):
            custom = f"data/overrides/{name}.svg"
            data = read(custom if custom in files else f"glyphs/{name}.svg")
            require(b"<!DOCTYPE" not in data.upper() and b"<!ENTITY" not in data.upper(), "Unsafe Kaiyuan SVG")
            root = ElementTree.fromstring(data)
            paths = [e for e in root if e.tag == "{http://www.w3.org/2000/svg}path"]
            require(root.tag == "{http://www.w3.org/2000/svg}svg" and len(paths) == len(root) == 1
                    and "transform" not in root.attrib and "transform" not in paths[0].attrib,
                    "Unexpected SVG outline structure for " + name)
            outline = RecordingPen()
            parse_path(paths[0].attrib["d"], TransformPen(outline, (1, 0, 0, -1, 0, 0)))
            bounds = BoundsPen(None)
            outline.replay(bounds)
            coords = bounds.bounds
            require(coords is not None and 0 <= coords[0] < coords[2] <= 1000
                    and -120 <= coords[1] < coords[3] <= 880, "Empty or out-of-bounds reviewed outline: " + name)
            pen = TTGlyphPen(None)
            outline.replay(Cu2QuPen(pen, max_err=1))
            glyphs[name] = pen.glyph()
        notices = {output: read(path) for output, path in NOTICE_PATHS.items()}

    order = [".notdef", *approved.values()]
    order = [order[0], *sorted(order[1:])]
    font = FontBuilder(1000, isTTF=True)
    font.setupGlyphOrder(order)
    font.setupCharacterMap(approved)
    font.setupGlyf(glyphs)
    font.setupHorizontalMetrics({g: (1000, 0) for g in order})
    font.setupHorizontalHeader(ascent=880, descent=-120)
    font.setupNameTable({
        "familyName": FAMILY, "styleName": "Regular", "psName": PS,
        "copyright": ATTRIBUTION, "fullName": FAMILY + " Regular",
        "uniqueFontIdentifier": PS + ";0.2.0", "version": "Version 0.2.0",
        "licenseDescription": "SIL Open Font License, Version 1.1.",
        "licenseInfoURL": "https://openfontlicense.org/",
        "description": "Human-approved only derivative of Kaiyuan Small Seal historical outlines.",
    })
    font.setupOS2(sTypoAscender=880, sTypoDescender=-120, sTypoLineGap=0,
                  usWinAscent=880, usWinDescent=120, fsType=0)
    font.setupPost()
    font.font.recalcTimestamp = False
    font.font["head"].created = font.font["head"].modified = 3872880000
    binary = io.BytesIO()
    font.save(binary)
    data = binary.getvalue()
    with TTFont(io.BytesIO(data), recalcTimestamp=False) as result:
        require(result.getBestCmap() == approved
                and all(table.cmap.items() <= approved.items() for table in result["cmap"].tables if table.isUnicode()),
                "Kaiyuan result encodes non-approved characters")
    return data, notices, approval
