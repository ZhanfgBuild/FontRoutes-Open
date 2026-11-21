import os
from pathlib import Path
import tempfile
import unittest
from xml.etree import ElementTree
from unittest.mock import patch

import build
import unicode18


class Unicode18Test(unittest.TestCase):
    def test_lxgw_seal_is_a_named_route_not_system_fallback(self):
        row = next(r for r in unicode18.lock()["fonts"] if r["filename"] == "LXGWSeal-Regular.ttf")
        original = b'<familyset><family name="sans-serif"><font>StockLatin.ttf</font></family><family><font>StockFallback.ttf</font></family></familyset>'
        actual = build.add_families(original, [row])
        routes = ElementTree.fromstring(actual)
        self.assertEqual(routes[0].attrib, {"name": "sans-serif"})
        self.assertEqual(routes[0].findtext("font"), "StockLatin.ttf")
        self.assertEqual([family.findtext("font") for family in routes if not family.get("name")],
                         ["StockFallback.ttf"])
        self.assertEqual(routes[-1].get("name"), "fontroutes-open-seal-lxgw")
        self.assertEqual(routes[-1].findtext("font"), "LXGWSeal-Regular.ttf")
        self.assertEqual(row["expected_outline_count"], 165)
        with self.assertRaisesRegex(ValueError, "collision"):
            build.add_families(actual, [row])

    def test_age_delta_not_whole_repertoire(self):
        self.assertEqual(unicode18.age_delta(b"0041 ; 1.1\n1234..1235 ; 18.0 # delta\n", 2), {0x1234, 0x1235})
        with self.assertRaisesRegex(ValueError, "Overlapping"):
            unicode18.age_delta(b"1234..1235 ; 18.0\n1235 ; 18.0\n", 2)
        with self.assertRaisesRegex(ValueError, "targets"):
            unicode18.age_delta(b"1234; 18.0", 2)

    def test_unicode_data_categories_are_lossless_for_ranges(self):
        lines = b"0041;LATIN CAPITAL LETTER A;Lu;\n1234;<Sample, First>;Lo;\n1236;<Sample, Last>;Lo;"
        self.assertEqual(unicode18.categories(lines, {0x41, 0x1235}), {0x41: "Lu", 0x1235: "Lo"})
        with self.assertRaisesRegex(ValueError, "Missing"):
            unicode18.categories(lines, {0x42})

    def test_gap_and_accepted_reports_are_separate(self):
        spec = {"expected_delta": 4, "data": {}}
        data = {
            "DerivedAge.txt": b"0041..0044;18.0",
            "UnicodeData.txt": b"0041;A;Lu;\n0042;B;Lu;\n0043;C;Lu;\n0044;CONTROL;Cf;",
            "Blocks.txt": b"0040..004F; Test",
        }
        mapped = {0x41: ["a"], 0x42: ["b"]}
        strong = {0x41: ["a"], 0x42: [], 0x43: [], 0x44: []}
        weak = {0x41: [], 0x42: [{"font": "b", "reason": "empty"}], 0x43: [], 0x44: []}
        with patch.object(unicode18, "outline_scan", return_value=(mapped, strong, weak, {})):
            report = unicode18.audit(spec, data, {"a": b""}, {"b": b""})
        self.assertEqual(report["baseline_accepted_graphic_outlines"], 1)
        self.assertEqual(report["counts"], {"outline": 1, "missing": 1, "mapped_without_accepted_outline": 1, "non_graphic": 1})

    @unittest.skipUnless(os.environ.get("FONTROUTES_U18_TEST") == "1", "fontTools outline test is opt-in")
    def test_known_tofu_and_highly_repeated_glyphs_are_rejected(self):
        from fontTools.fontBuilder import FontBuilder
        from fontTools.pens.ttGlyphPen import TTGlyphPen

        def triangle(point):
            pen = TTGlyphPen(None)
            if point:
                pen.moveTo((0, 0))
                pen.lineTo((point, 200))
                pen.lineTo((200, 0))
                pen.closePath()
            return pen.glyph()
        fb = FontBuilder(1000, isTTF=True)
        names = [".notdef", "one", "empty", "template"]
        fb.setupGlyphOrder(names)
        fb.setupCharacterMap({0x5000: "one", 0x5001: "empty", 0x5002: ".notdef",
                              **{code: "template" for code in range(0x5003, 0x500B)}})
        fb.setupGlyf({".notdef": triangle(15), "one": triangle(85),
                      "empty": triangle(0), "template": triangle(99)})
        fb.setupHorizontalMetrics({name: (400, 0) for name in names})
        fb.setupHorizontalHeader(ascent=600, descent=-200)
        fb.setupNameTable({"familyName": "Synthetic fixture only", "styleName": "Regular"})
        fb.setupOS2(sTypoAscender=600, sTypoDescender=-200, usWinAscent=600, usWinDescent=200)
        fb.setupPost()
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "test.ttf"
            fb.save(p)
            _, strong, weak, fonts = unicode18.outline_scan({"test.ttf": p.read_bytes()}, set(range(0x5000, 0x500B)))
        self.assertEqual(set(strong), {0x5000})
        self.assertGreaterEqual(len(weak), 9)
        self.assertEqual(fonts["test.ttf"]["rejected"]["high_duplicate_outline"], 8)


if __name__ == "__main__":
    unittest.main()
