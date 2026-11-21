import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

import build


ROOT = Path(__file__).resolve().parents[1]
XML = b"""<?xml version="1.0" encoding="utf-8"?>
<familyset version="23">
  <!-- OEM fixture, contains no ROM data -->
  <family name="sans-serif">
    <font weight="400" style="normal" supportedAxes="wght">Stub.ttf<axis tag="wght" stylevalue="400"/></font>
  </family>
  <alias name="system-alias" to="sans-serif"/>
</familyset>
"""
ROWS = [{"family": "fontroutes-open-sans", "filename": "Demo.ttf", "weight": 400, "style": "normal"}]


class FontRoutesTest(unittest.TestCase):
    def test_insertion_is_only_byte_difference(self):
        output = build.add_families(XML, ROWS)
        start = output.index(b"\n  <!-- FontRoutes Open")
        end = output.index(b"</familyset>")
        self.assertEqual(output[:start] + output[end:], XML)
        self.assertIn(b'supportedAxes="wght"', output)
        self.assertIn(b'<family name="fontroutes-open-sans">', output)

    def test_fails_closed_for_foreign_xml_and_doctype(self):
        for data in (
            b"<root/>",
            b"<familyset/>",
            b"<familyset><family/></familysetX>",
            b'<!DOCTYPE familyset [<!ENTITY test "test">]><familyset><family/></familyset>',
        ):
            with self.subTest(data=data), self.assertRaises((ValueError, build.expat.ExpatError)):
                build.add_families(data, ROWS)

    def test_collision_and_bad_filename_rejected(self):
        for data, rows in (
            (XML.replace(b"system-alias", b"fontroutes-open-sans"), ROWS),
            (XML.replace(b'to="sans-serif"', b'to="fontroutes-open-sans"'), ROWS),
            (XML.replace(b'supportedAxes="wght"', b'fallbackFor="fontroutes-open-sans"'), ROWS),
            (XML, [{**ROWS[0], "filename": "../Demo.ttf"}]),
        ):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                build.add_families(data, rows)

    def test_archive_is_deterministic_and_no_traversal(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            one, two = root / "one.zip", root / "two.zip"
            build.pack(one, {"abc.txt": b"def"})
            build.pack(two, {"abc.txt": b"def"})
            self.assertEqual(one.read_bytes(), two.read_bytes())
            with self.assertRaises(ValueError):
                build.pack(root / "bad.zip", {"../abc.txt": b"def"})

    def test_sha_mismatch_with_offline_cache_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ("0" * 64)).write_bytes(b"bad")
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                build.fetch_asset({
                    "url": "https://raw.githubusercontent.com/example/proj/" + "1" * 40 + "/font.ttf",
                    "sha256": "0" * 64,
                }, root, offline=True)

    def test_public_command_rejects_device_inputs_and_local_public_dir(self):
        commands = [
            ["inputs", "--rom-xml", "foo.xml"],
            ["local-module", "--rom-xml", "foo.xml", "--rom-xml-sha256", "0" * 64,
             "--product", "enchilada", "--sdk", "37", "--fingerprint-file", "bar.txt",
             "--i-accept-local-rom-overlay", "--out", str(ROOT / "dist" / "bad")],
        ]
        for args in commands:
            result = subprocess.run([sys.executable, str(ROOT / "build.py"), "--profile", "op6-slim", *args],
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("public", result.stderr.lower())

    @unittest.skipUnless(shutil.which("sh") and shutil.which("sha256sum"), "shell checksum tools unavailable")
    def test_installer_rejects_wrong_device_sdk_rom_and_mount(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fp = b"test/phone/example:17/mock/test:user/test-keys"
            rom = root / "baseline.xml"
            rom.write_bytes(XML)
            mod = root / "module"
            (mod / "system/etc").mkdir(parents=True)
            (mod / "system/etc/font_fallback.xml").write_bytes(XML)
            raw = build.installer("enchilada", 37, build.digest(fp), build.digest(XML), ["Demo.ttf"])
            raw = raw.replace(("'" + build.ROM_PATH + "'").encode(), ("'" + str(rom) + "'").encode()).replace(b"/system/fonts/$name", (str(root) + "/$name").encode())
            scripts = root / "script"
            scripts.write_bytes(raw)
            pre = '''
abort() { printf '%s\n' "$*"; exit 9; }
ui_print() { printf '%s\n' "$*"; }
getprop() {
  case "$1" in
    ro.product.device) printf '%s\n' "$TEST_PRODUCT";;
    ro.build.version.sdk) printf '%s\n' "$TEST_SDK";;
    ro.build.fingerprint) printf '%s\n' "$TEST_FP";;
  esac
}
. "$TEST_SCRIPT"
'''
            env = dict(os.environ, TEST_PRODUCT="enchilada", TEST_SDK="37", TEST_FP=fp.decode(),
                       BOOTMODE="true", KSU="true", MODPATH=str(mod), TEST_SCRIPT=str(scripts))
            valid = subprocess.run(["sh", "-c", pre], env=env, capture_output=True, text=True)
            self.assertEqual(valid.returncode, 0, valid.stdout + valid.stderr)
            for key, value in {"TEST_PRODUCT": "dodge", "TEST_SDK": "36", "TEST_FP": "different", "BOOTMODE": "false", "KSU": "false"}.items():
                failed = subprocess.run(["sh", "-c", pre], env={**env, key: value}, capture_output=True)
                self.assertEqual(failed.returncode, 9)
            (root / "Demo.ttf").touch()
            failed = subprocess.run(["sh", "-c", pre], env=env, capture_output=True)
            self.assertIn(b"already mounted", failed.stdout)
            (root / "Demo.ttf").unlink()
            rom.write_bytes(b"modified")
            failed = subprocess.run(["sh", "-c", pre], env=env, capture_output=True)
            self.assertIn(b"ROM XML mismatch", failed.stdout)


if __name__ == "__main__":
    unittest.main()
