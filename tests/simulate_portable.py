"""Run final package scripts against isolated ROM paths and the actual packaged ARM64 helper."""
import argparse
import hashlib
import os
from pathlib import Path
import shlex
import stat
import subprocess
import tempfile
import zipfile
from io import BytesIO

STOCK = b'<?xml version="1.0"?>\r\n<familyset version="23">\r\n<!-- OEM order </familyset> -->\r\n<family><font weight="400">OEM.ttf</font></family>\r\n<alias name="oem-alias" to="oem-target"/>\r\n</familyset>\r\n'


def verify(path, profile, private):
    raw = path.read_bytes()
    assert path.with_suffix(".zip.sha256").read_text().split()[0] == hashlib.sha256(raw).hexdigest()
    with zipfile.ZipFile(BytesIO(raw)) as z:
        assert z.testzip() is None
        names = set(z.namelist())
        assert names == set(info.filename for info in z.infolist()) and len(names) == len(z.infolist())
        assert all(stat.S_ISREG(info.external_attr >> 16) for info in z.infolist())
        assert {"module.prop", "customize.sh", "post-fs-data.sh", "bin/fr-xml", "FAMILIES.xml"} <= names
        assert not any(name.startswith("system/etc/") or name in {"skip_mount", "disable", "replace"} or ".replace" in name for name in names)
        assert ("system/fonts/FontRoutesOpen-NotoColorEmoji.ttf" in names) == (profile == "op13-full")
        assert ("system/fonts/ArchaicCuneiformNumerals.ttf" in names) == (profile == "op13-full")
        assert "system/fonts/FontRoutes-Tangut-IDS-Trial.ttf" not in names
        assert not any("unifont" in name.lower() for name in names)
        if private:
            assert "licenses/Compatibility-Mark-MODIFICATIONS.txt" in names
            assert z.read("FAMILIES.xml").count(b"<family>") == 1
        else:
            assert "licenses/Compatibility-Mark-MODIFICATIONS.txt" not in names
            assert b"<family>" not in z.read("FAMILIES.xml")
        print(f"{path.name}: CRC, size, manifest shape, offline module ZIP layout passed", flush=True)


def scenario(path, profile, runner, *, sdk=None, product=None, provider=True, ambiguous=False, existing=False,
             corrupt=False, bad_xml=False, wrong_manager=False, wrong_abi=False, legacy_alias=False, symlink=False,
             boot_change=None, expect_install=True, cold_guard=False, competing=False):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        stage = tmp / "stage"
        with zipfile.ZipFile(path) as z:
            z.extractall(stage)
        overlay = tmp / "adb/metamodule"
        overlay.mkdir(parents=True)
        if provider:
            (overlay / "metamount.sh").write_text("# indicator only; must not be executed\nexit 42\n")
        system = tmp / "system"
        (system / "etc").mkdir(parents=True)
        (system / "fonts").mkdir()
        if sdk is None:
            sdk = 37 if profile == "op13-full" else 34
        if product is None:
            product = "PJZ110" if profile == "op13-full" else "enchilada"
        config = "fonts.xml" if sdk <= 34 else "font_fallback.xml"
        rom = system / "etc" / config
        previous = STOCK
        if legacy_alias:
            previous = STOCK.replace(b"oem-target", b"fontroutes-open-sans")
        if bad_xml:
            previous = b'<!DOCTYPE familyset><familyset><family/></familyset>'
        if symlink:
            target = system / "etc" / "real.xml"
            target.write_bytes(previous)
            rom.symlink_to(target)
        else:
            rom.write_bytes(previous)
        if ambiguous:
            (system / "etc" / "font_fallback.xml").write_bytes(STOCK)
        if existing:
            (system / "fonts" / "FontRoutesOpen-NotoSans-Regular.ttf").write_text("already mounted")
        if corrupt:
            (stage / "system/fonts/FontRoutesOpen-NotoSans-Regular.ttf").write_text("modified")
        def conflict():
            meta = tmp / "adb/modules/other-font"
            meta.mkdir(parents=True)
            (meta / "module.prop").write_text("id=other-font")
            elsewhere = overlay / "mnt/other-font/system/etc"
            elsewhere.mkdir(parents=True)
            (elsewhere / config).write_bytes(STOCK)
        if competing:
            conflict()
        before = {str(p): p.read_bytes() for p in system.rglob("*") if p.is_file()}
        # Change only absolute fixture paths and execution shim, not any archive content on disk.
        def redirect(text):
            for old, new in [("/system/etc/", system / "etc"),
                             ("/data/adb/metamodule/", overlay), ("/data/adb/modules/", tmp / "adb/modules"),
                             ("/data/adb/modules_update/", tmp / "adb/modules_update")]:
                text = text.replace(old, f"{new}/")
            text = text.replace('"/system/fonts/$name"', f'"{system}/fonts/$name"')
            for variable in ("MODPATH", "content"):
                text = text.replace(f'${variable}' + str(system / "etc") + '/', f'${variable}/system/etc/')
            return text
        customize = redirect((stage / "customize.sh").read_text()).replace(
            '"$MODPATH/bin/fr-xml" "$rom"', f'{shlex.quote(runner)} "$MODPATH/bin/fr-xml" "$rom"')
        methods = """
abort() { printf '%s\\n' "$*" >&2; exit 21; }
ui_print() { printf '%s\\n' "$*"; }
set_perm() { chmod "$4" "$1"; }
getprop() { case "$1" in ro.product.device) printf '%s\\n' "$FR_PRODUCT";; ro.build.version.sdk) printf '%s\\n' "$FR_SDK";;
 ro.build.fingerprint) printf '%s\\n' "$FR_FINGERPRINT";; esac; }
"""
        env = dict(os.environ, MODPATH=str(stage), BOOTMODE="true", KSU="false" if wrong_manager else "true",
                   ARCH="arm" if wrong_abi else "arm64", FR_PRODUCT=product, FR_SDK=str(sdk),
                   FR_FINGERPRINT="OEM/model/official-17-release-keys")
        run = subprocess.run(["sh", "-c", methods + customize], env=env, capture_output=True, text=True)
        assert (run.returncode == 0) == expect_install, (run.stdout, run.stderr)
        assert {str(p): p.read_bytes() for p in system.rglob("*") if p.is_file()} == before
        patched = stage / "system/etc" / config
        if not expect_install:
            assert not patched.exists(), run.stderr
            return
        add = (stage / "FAMILIES.xml").read_bytes()
        at = previous.rindex(b"</familyset>")
        assert patched.read_bytes() == previous[:at] + add + previous[at:]
        boot = stage / "boot-fixture.sh"
        boot.write_text(methods + redirect((stage / "post-fs-data.sh").read_text()))
        if boot_change == "fingerprint":
            env["FR_FINGERPRINT"] = "OEM/model/OTA-18-release-keys"
        elif boot_change == "xml":
            rom.write_bytes(STOCK.replace(b"OEM.ttf", b"CHANGED.ttf"))
        elif boot_change == "provider":
            (overlay / "disable").touch()
        elif boot_change == "conflict":
            conflict()
        elif boot_change == "guard":
            (stage / "ROM-GUARD.txt").unlink()
        if cold_guard:
            (stage / "skip_mount").touch()
        run = subprocess.run(["sh", str(boot)], env=env, capture_output=True, text=True)
        assert run.returncode == 0, (run.stdout, run.stderr)
        assert (stage / "skip_mount").exists() == (bool(boot_change) or cold_guard)
        assert (stage / "ROM-GUARD-STATUS.txt").exists() == bool(boot_change)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("zip", type=Path)
    ap.add_argument("profile", choices=["op6-slim", "op13-full"])
    ap.add_argument("--runner", default="qemu-aarch64")
    ap.add_argument("--private", action="store_true")
    args = ap.parse_args()
    verify(args.zip, args.profile, args.private)
    for case in ({}, {"sdk": 37}, {"sdk": 36}, {"boot_change": "fingerprint"}, {"boot_change": "xml"},
                 {"boot_change": "provider"}, {"boot_change": "conflict"}, {"boot_change": "guard"}, {"cold_guard": True}):
        scenario(args.zip, args.profile, args.runner, **case)
    for case in ({"sdk": 35}, {"sdk": 27}, {"sdk": 38}, {"product": "different-device"}, {"provider": False},
                 {"existing": True}, {"corrupt": True}, {"bad_xml": True}, {"wrong_manager": True},
                 {"wrong_abi": True}, {"competing": True}, {"legacy_alias": True}, {"symlink": True}):
        scenario(args.zip, args.profile, args.runner, expect_install=False, **case)
    if args.profile == "op6-slim":
        scenario(args.zip, args.profile, args.runner, sdk=34, ambiguous=True, expect_install=False)
    print("Install simulation, stock bytes preserved, OTA/mount guard and refusal cases passed.", flush=True)


if __name__ == "__main__":
    main()
