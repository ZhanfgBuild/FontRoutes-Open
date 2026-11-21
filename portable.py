"""Build a self-contained offline KernelSU module; no device ROM is accepted as input."""
import argparse
from pathlib import Path
import struct

import build
import unicode18

VERSION = "0.3.0"
HERE = Path(__file__).resolve().parent
INSTALLER = HERE / "installer"


def read_helper(path):
    """Require a bounded 64-bit little-endian AArch64 Linux ELF without PT_INTERP."""
    build.require(64 <= path.stat().st_size <= 4 * 1024 * 1024, "Unexpected ARM64 helper size")
    data = path.read_bytes()
    build.require(data[:7] == b"\x7fELF\x02\x01\x01" and struct.unpack_from("<H", data, 18)[0] == 183,
                  "Helper must be a Linux AArch64 ELF")
    phoff = struct.unpack_from("<Q", data, 32)[0]
    phsize, phcount = struct.unpack_from("<HH", data, 54)
    build.require(phsize >= 56 and phcount > 0 and phoff + phsize * phcount <= len(data)
                  and all(struct.unpack_from("<I", data, phoff + phsize * i)[0] != 3 for i in range(phcount)),
                  "Helper must be statically linked without an interpreter")
    return data


def module(profile, cache, offline, helper, private_logo=False):
    rows, fonts, licenses, license_src = build.sources(profile, cache, offline)
    public_sources = {"base": rows.copy(), "base_licenses": license_src}
    if profile == "op13-full":
        emoji, notice = build.get_library_inputs("emoji", cache, offline)
        emoji_spec = build.library_spec("emoji")
        emoji_name = "FontRoutesOpen-NotoColorEmoji.ttf"
        fonts[emoji_name] = emoji
        rows.append({"family": "fontroutes-open-emoji-u18", "style": "normal", "weight": 400, "filename": emoji_name})
        licenses["NotoColorEmoji-OFL.txt"] = notice
        public_sources["named_emoji"] = emoji_spec
        spec = unicode18.lock()
        extra_fonts, extra_notices, provenance = unicode18.supplement_assets(spec, cache, offline)
        eligible = [row for row in spec["fonts"] if row.get("mount", True) and row.get("asset_type") != "tangut-ids-trial"]
        build.require(not set(fonts).intersection(row["filename"] for row in eligible), "Unicode 18 tries to replace base fonts")
        fonts.update({row["filename"]: extra_fonts[row["filename"]] for row in eligible})
        keep = {row["license"] for row in eligible} | set(spec["kaiyuan_reviewed_source"]["upstream_notices"])
        licenses.update({name: extra_notices[name] for name in keep})
        licenses["LICENSE-UNICODE.txt"] = (HERE / "licenses/UNICODE-DATA-LICENSE.txt").read_bytes()
        rows.extend(eligible)
        public_sources["named_unicode18"] = eligible
        public_sources["reviewed_seal_source"] = spec["kaiyuan_reviewed_source"]
        public_sources["excluded_from_android_routing"] = [row["filename"] for row in spec["fonts"] if row not in eligible]
    if private_logo:
        from logo_fallback import derive
        brand, notice = build.get_library_inputs("logos", cache, offline)
        fonts[build.LOGO_OUT] = derive(brand)
        licenses["Font-Awesome-LICENSE.txt"] = notice
        licenses["Compatibility-Mark-MODIFICATIONS.txt"] = (
            b"Apple-representing glyph from Font Awesome Free 7.3.1 Brands, subset/remapped U+F179 to U+F8FF; "
            b"renamed family, CFF and PostScript. SIL OFL 1.1, attribution in upstream license. "
            b"The underlying mark is a third-party trademark, not licensed or endorsed by this package. "
            b"This package is for personal device use; do not redistribute it.\n"
        )
        public_sources["private_compatibility_mark"] = build.library_spec("logos")
    anchor = b'<familyset><family><font>fixture.ttf</font></family></familyset>'
    patched = build.add_families(anchor, rows, include_logo=private_logo)
    at = anchor.index(b"</familyset>")
    fragment = patched[at:-len(b"</familyset>")]
    entries = {
        "bin/fr-xml": helper, "FAMILIES.xml": fragment,
        "customize.sh": (INSTALLER / "customize.sh").read_bytes().replace(b"@PROFILE@", profile.encode()),
        "post-fs-data.sh": (INSTALLER / "post-fs-data.sh").read_bytes(),
        "BUILDER-MIT-LICENSE.txt": (HERE / "LICENSE").read_bytes(),
        "licenses/GO-BSD-LICENSE.txt": (HERE / "licenses/GO-BSD-LICENSE.txt").read_bytes(),
        "SOURCES.json": build.encode_json(public_sources),
        "module.prop": (
            f"id=fontroutes-open-{profile}\nname=FontRoutes Open ({profile})\n"
            f"version={VERSION}\nversionCode=300\nauthor=FontRoutes Open contributors\n"
            "description=Adds named fonts; stock fonts unchanged. Disable before OTA. Reboot after install.\n"
        ).encode(),
        "README.txt": (
            f"Complete offline KernelSU install ZIP, {profile}. No companion patch needed.\n"
            "Requires ARM64 and enabled system mount provider (KernelSU metamodule).\n"
            "Supported selection: OP13 SDK 36-37; OP6 SDK 28-34 (legacy fonts.xml) or 36-37. API 35 refused.\n"
            "Reboot after installation. System defaults are preserved. Named families require app support.\n"
            "On conflict or before OTA, disable this and other font modules and reboot. Recover by disabling/removing this module.\n"
            + ("Personal build: appended F8FF compatibility fallback after OEM routes. Do not redistribute.\n" if private_logo
               else "Public build: no added trademark glyph; retains the ROM's existing fallback.\n")
        ).encode(),
        **{f"system/fonts/{name}": data for name, data in fonts.items()},
        **{f"licenses/{name}": data for name, data in licenses.items()},
    }
    if profile == "op13-full":
        entries["provenance/Kaiyuan-approved.csv"] = provenance["Kaiyuan-approved.csv"]
    entries["PAYLOAD-SHA256SUMS.txt"] = "".join(f"{build.digest(data)}  {name}\n"
                                                   for name, data in sorted(entries.items())).encode()
    return entries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=build.DEVICES, required=True)
    parser.add_argument("--helper", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=HERE / "dist")
    parser.add_argument("--cache", type=Path, default=HERE / ".cache")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--private-logo", action="store_true", help="personal build only; must save outside dist")
    ns = parser.parse_args()
    build.require(not ns.private_logo or (ns.out.resolve() != HERE and not ns.out.resolve().is_relative_to(HERE)),
                  "Personal logo builds must use a separate output directory outside the repository")
    suffix = "PERSONAL-KSU" if ns.private_logo else "PUBLIC-KSU"
    entries = module(ns.profile, ns.cache, ns.offline, read_helper(ns.helper), ns.private_logo)
    build.pack(ns.out / f"FontRoutes-Open-{ns.profile}-{suffix}-{VERSION}.zip", entries)


if __name__ == "__main__":
    main()
