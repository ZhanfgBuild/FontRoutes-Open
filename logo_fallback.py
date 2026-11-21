"""Build a single-codepoint local compatibility mark from the pinned Brands OTF."""

from io import BytesIO

LOGO_OUT = "FontRoutesOpen-CompatibilityMark-Regular.otf"
FAMILY = "FontRoutes Open Compatibility Mark"
POSTSCRIPT = "FontRoutesOpen-CompatibilityMark-Regular"
SOURCE = 0xF179
TARGET = 0xF8FF


def derive(source):
    # Kept lazy: public font builds, app libraries, and preflight do not need fontTools.
    try:
        from fontTools import subset
        from fontTools.pens.recordingPen import RecordingPen
        from fontTools.ttLib import TTFont
        from fontTools.ttLib.tables._c_m_a_p import CmapSubtable
    except ImportError as e:
        raise ValueError("Install the local logo builder dependencies from requirements-logo.txt") from e

    font = TTFont(BytesIO(source), recalcTimestamp=False)
    glyph = font.getBestCmap().get(SOURCE)
    if glyph != "apple" or "CFF " not in font:
        raise ValueError("Pinned Brands source no longer contains the expected mark")
    outline = RecordingPen()
    font.getGlyphSet()[glyph].draw(outline)
    advance = font["hmtx"].metrics[glyph]

    cfg = subset.Options()
    cfg.name_IDs = ["*"]
    cfg.name_legacy = True
    cfg.name_languages = ["*"]
    cfg.glyph_names = True
    cfg.layout_features = []
    cfg.drop_tables += ["GSUB", "GPOS", "GDEF", "DSIG", "kern"]
    sub = subset.Subsetter(options=cfg)
    sub.populate(unicodes={SOURCE})
    sub.subset(font)

    # Use exactly two BMP Unicode views, both with the same single visible codepoint.
    tables = []
    for platform, encoding in ((0, 3), (3, 1)):
        cmap = CmapSubtable.newSubtable(4)
        cmap.platformID = platform
        cmap.platEncID = encoding
        cmap.language = 0
        cmap.cmap = {TARGET: glyph}
        tables.append(cmap)
    font["cmap"].tables = tables

    renamed = {
        1: FAMILY,
        2: "Regular",
        3: POSTSCRIPT + ";0.2.0",
        4: FAMILY + " Regular",
        5: "Version 0.2.0",
        6: POSTSCRIPT,
        10: "Local single-mark compatibility subset; modified from Font Awesome Free 7.3.1 Brands.",
        13: "SIL Open Font License 1.1; see included upstream notice.",
        14: "https://openfontlicense.org/",
        16: FAMILY,
        17: "Regular",
        18: FAMILY + " Regular",
        20: POSTSCRIPT,
        21: FAMILY,
        22: "Regular",
        25: POSTSCRIPT,
    }
    names = font["name"]
    for entry in tuple(names.names):
        args = (entry.nameID, entry.platformID, entry.platEncID, entry.langID)
        if entry.nameID in renamed:
            names.setName(renamed[entry.nameID], *args)
    for idx in (1, 2, 3, 4, 5, 6, 10, 13, 14, 16, 17):
        names.setName(renamed[idx], idx, 3, 1, 0x0409)
    cff = font["CFF "].cff
    top = cff[cff.fontNames[0]]
    cff.fontNames[0] = POSTSCRIPT
    top.FamilyName = FAMILY
    top.FullName = FAMILY + " Regular"
    font["OS/2"].usFirstCharIndex = TARGET
    font["OS/2"].usLastCharIndex = TARGET

    data = BytesIO()
    font.save(data)
    result = data.getvalue()
    check = TTFont(BytesIO(result), recalcTimestamp=False)
    if (
        set(check.getGlyphOrder()) != {".notdef", glyph}
        or [(t.platformID, t.platEncID, t.cmap) for t in check["cmap"].tables]
        != [(0, 3, {TARGET: glyph}), (3, 1, {TARGET: glyph})]
        or check["CFF "].cff.fontNames != [POSTSCRIPT]
        or any(k in check for k in ("GSUB", "GDEF", "GPOS", "kern"))
        or check["hmtx"].metrics[glyph] != advance
        or not any(n.nameID == 0 and "Font Awesome" in n.toUnicode() for n in check["name"].names)
    ):
        raise ValueError("Generated compatibility mark did not pass one-codepoint/attribution QA")
    outline_check = RecordingPen()
    check.getGlyphSet()[glyph].draw(outline_check)
    if outline_check.value != outline.value:
        raise ValueError("Generated compatibility mark did not retain the original outline")
    for n in check["name"].names:
        if n.nameID in {1, 3, 4, 6, 16, 18, 20, 21, 25} and "fontawesome" in n.toUnicode().replace(" ", "").lower():
            raise ValueError("Generated compatibility mark retained a reserved primary name")
    return result
