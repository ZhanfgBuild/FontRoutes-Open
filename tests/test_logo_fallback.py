import io
import os
import tempfile
import unittest
from pathlib import Path

import build
from logo_fallback import LOGO_OUT, POSTSCRIPT, TARGET, derive


class LocalLogoTest(unittest.TestCase):
    def test_appends_one_unnamed_route_and_preserves_bytes(self):
        original = b"""<familyset>
  <family name="sans-serif"><font>OEM.ttf</font></family>
  <family><font>ExistingFallback.ttf</font></family>
  <alias name="alias" to="sans-serif"/>
</familyset>
"""
        name = LOGO_OUT.encode()
        without = build.add_families(original, [], include_logo=False)
        self.assertNotIn(name, without)
        with_logo = build.add_families(original, [], include_logo=True)
        start = with_logo.index(b"\n  <!-- FontRoutes Open:")
        tail = with_logo.index(b"</familyset>")
        self.assertEqual(with_logo[:start] + with_logo[tail:], original)
        self.assertIn(b"<family>\n    <font", with_logo[start:tail])
        self.assertEqual(with_logo[start:tail].count(name), 1)
        self.assertIn(f'postScriptName="{POSTSCRIPT}"'.encode(), with_logo)
        with self.assertRaisesRegex(ValueError, "already references"):
            build.add_families(with_logo, [], include_logo=True)

    @unittest.skipUnless(os.environ.get("FONTROUTES_INTEGRATION_ASSETS") == "1", "pinned upstream integration is opt-in")
    def test_real_brand_font_cmap_and_outline(self):
        from fontTools.ttLib import TTFont

        with tempfile.TemporaryDirectory() as t:
            package = os.environ.get("FONTROUTES_TEST_LOGOS_ZIP")
            src, notice = build.get_library_inputs("logos", Path(t), bool(package), Path(package) if package else None)
        self.assertIn(b"SIL OPEN FONT LICENSE", notice.upper())
        result = derive(src)
        self.assertEqual(derive(src), result)
        f = TTFont(io.BytesIO(result), recalcTimestamp=False)
        self.assertEqual(f.getBestCmap(), {TARGET: "apple"})
        self.assertEqual([t.cmap for t in f["cmap"].tables], [{TARGET: "apple"}, {TARGET: "apple"}])
        self.assertEqual(set(f.getGlyphOrder()), {".notdef", "apple"})
        self.assertEqual(f["name"].getDebugName(6), POSTSCRIPT)


if __name__ == "__main__":
    unittest.main()
