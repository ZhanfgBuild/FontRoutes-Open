#!/usr/bin/env python3
"""Build public FontRoutes inputs or an explicitly local, ROM-bound module."""
import argparse
import hashlib
import io
import json
import os
import re
import stat
import sys
import tempfile
import urllib.request
import zipfile
from collections import defaultdict
from pathlib import Path
from xml.parsers import expat

HERE = Path(__file__).resolve().parent
# Read the literal model or codename returned by ro.product.device, not by guessing the market.
DEVICES = {
    "op13-full": ({"PJZ110", "CPH2649", "CPH2653", "CPH2655", "OP5D0DL1", "dodge", "OnePlus13"}, {35, 36, 37}),
    "op6-slim": ({"A6000", "A6003", "enchilada", "OnePlus6"}, {37}),
}
VERSION = "0.1.0"
MAX_ASSET = 40 * 1024 * 1024
MAX_XML = 2 * 1024 * 1024
ROM_PATH = "/system/etc/font_fallback.xml"
SHA = r"[0-9a-f]{64}"
SAFE_FILE = r"[A-Za-z0-9][A-Za-z0-9_.-]{0,100}"
SAFE_FAMILY = r"fontroutes-open-[a-z0-9-]{1,32}"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def safe_name(value, pattern):
    require(isinstance(value, str) and re.fullmatch(pattern, value), "Invalid manifest identifier")
    return value


def approved_asset_url(url):
    """Only immutable GitHub raw paths described by the checked-in lock."""
    return bool(re.fullmatch(
        r"https://raw\.githubusercontent\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/[0-9a-f]{40}/[A-Za-z0-9_./%+-]+", url
    )) and not {"..", "."}.intersection(url.split("/"))


def fetch_asset(spec, cache, offline=False):
    sha = safe_name(spec["sha256"], SHA)
    url = spec["url"]
    require(approved_asset_url(url), "Source URL must use a pinned public commit")
    target = cache / sha
    if target.exists():
        require(target.stat().st_size <= MAX_ASSET, "Cached input too large")
        data = target.read_bytes()
        require(digest(data) == sha, f"Cached input SHA-256 mismatch: {sha}")
        return data
    require(not offline, f"Offline source is missing: {sha}")
    with urllib.request.urlopen(url, timeout=60) as response:
        require(response.geturl() == url, "Unexpected asset redirect")
        data = response.read(MAX_ASSET + 1)
    require(0 < len(data) <= MAX_ASSET and digest(data) == sha, f"Source SHA-256 mismatch: {url}")
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=cache, delete=False) as handle:
        handle.write(data)
        tmp = handle.name
    os.replace(tmp, target)
    return data


def profile_lock(profile):
    lock = json.loads((HERE / "fonts.lock.json").read_text("utf-8"))
    require(lock.get("schema") == 1, "Unsupported lock format")
    entries = [item for item in lock["sources"] if profile in item["profiles"]]
    require(bool(entries), "Empty profile")
    return entries, lock["licenses"]


def checked_input_zip(path, profile, destination):
    """Seed a disposable cache with verified asset entries only, never extract paths."""
    rows, licenses = profile_lock(profile)
    expected = {f"fonts/{row['filename']}": row for row in rows}
    expected.update({f"licenses/{name}": licenses[name] for name in {r["license"] for r in rows}})
    require(path.stat().st_size <= MAX_ASSET * 2, "Public input ZIP too large")
    with zipfile.ZipFile(path) as z:
        entries = z.infolist()
        require(len(entries) == len(expected) + 2, "Unexpected public input ZIP entry count")
        require({i.filename for i in entries} == set(expected) | {"SOURCES.json", "README.txt"}, "Unexpected or repeated public input ZIP members")
        for member, spec in expected.items():
            info = z.getinfo(member)
            require(0 < info.file_size <= MAX_ASSET and not info.flag_bits & 1, "Invalid public input ZIP asset")
            require(stat.S_ISREG(info.external_attr >> 16), "Public input ZIP assets must be ordinary files")
            value = z.read(info)
            sha = safe_name(spec["sha256"], SHA)
            require(digest(value) == sha, f"Public input ZIP SHA-256 mismatch: {member}")
            (destination / sha).write_bytes(value)


def sources(profile, cache, offline):
    entries, licenses = profile_lock(profile)
    payload, license_data, seen = {}, {}, set()
    for row in entries:
        name = safe_name(row["filename"], SAFE_FILE)
        family = safe_name(row["family"], SAFE_FAMILY)
        style, weight = row["style"], row["weight"]
        require(style in ("normal", "italic") and type(weight) is int and 100 <= weight <= 900, "Invalid font route")
        key = family, style, weight
        require(key not in seen and name not in payload, "Duplicate font route or file")
        seen.add(key)
        font = fetch_asset(row, cache, offline)
        require(font[:4] in (b"\x00\x01\x00\x00", b"OTTO", b"ttcf"), "Input is not an OpenType font")
        payload[name] = font
        license_file = safe_name(row["license"], SAFE_FILE)
        if license_file not in license_data:
            lic = fetch_asset(licenses[license_file], cache, offline)
            require(b"SIL OPEN FONT LICENSE" in lic.upper() and b"1.1" in lic, "Missing OFL 1.1 license")
            license_data[license_file] = lic
    return entries, payload, license_data, {name: licenses[name] for name in license_data}


def encode_json(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True) + "\n").encode()


def pack(dest, entries):
    """Stable filenames, file modes, order and DOS timestamps, no absolute input paths."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in sorted(entries.items()):
            require(all(re.fullmatch(SAFE_FILE, part) for part in name.split("/")), "Unsafe archive path")
            item = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            item.create_system = 3
            item.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(item, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=6)
    data = buffer.getvalue()
    with zipfile.ZipFile(io.BytesIO(data)) as final:
        require(final.testzip() is None and final.namelist() == sorted(entries), "Archive integrity failed")
        require(all(final.read(name) == value for name, value in entries.items()), "Archive data changed")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=dest.parent, delete=False) as handle:
        handle.write(data)
        tmp = handle.name
    os.replace(tmp, dest)
    dest.with_suffix(".zip.sha256").write_text(f"{digest(data)}  {dest.name}\n", encoding="utf-8")
    print(f"{dest.name}: {len(data)} bytes, SHA-256 {digest(data)}")


def inspect_xml(xml):
    """Return the root-close byte offset and existing names; reject DTDs and non-UTF-8."""
    require(0 < len(xml) <= MAX_XML and not xml.startswith(b"\xef\xbb\xbf"), "Unsupported XML size or BOM")
    xml.decode("utf-8")
    p = expat.ParserCreate(encoding="UTF-8")
    names, depth, close, family_count = set(), 0, None, 0

    def start(name, attrs):
        nonlocal depth, family_count
        if depth == 0:
            require(name == "familyset" and "xmlns" not in attrs, "ROM font XML root is not a plain familyset")
        if depth == 1:
            if name in ("family", "family-list"):
                family_count += 1
            if attrs.get("name"):
                names.add(attrs["name"])
        # Avoid making a formerly dangling OEM alias or fallbackFor become active.
        for key in ("to", "fallbackFor"):
            if attrs.get(key):
                names.add(attrs[key])
        depth += 1

    def end(name):
        nonlocal depth, close
        depth -= 1
        if depth == 0:
            close = p.CurrentByteIndex

    def no_dtd(*_):
        raise ValueError("DTD and entity declarations are not accepted")

    p.StartElementHandler = start
    p.EndElementHandler = end
    p.StartDoctypeDeclHandler = no_dtd
    p.EntityDeclHandler = no_dtd
    p.ExternalEntityRefHandler = no_dtd
    p.Parse(xml, True)
    require(close is not None and xml[close:].startswith(b"</familyset") and family_count > 0, "Unsupported empty/closed familyset layout")
    return close, names, family_count


def add_families(xml, rows):
    at, names, count = inspect_xml(xml)
    families = defaultdict(list)
    for row in rows:
        family = safe_name(row["family"], SAFE_FAMILY)
        require(family not in names, f"ROM name collision: {family}")
        name = safe_name(row["filename"], SAFE_FILE)
        weight, style = row["weight"], row["style"]
        require(type(weight) is int and 100 <= weight <= 900 and style in ("normal", "italic"), "Invalid font route")
        families[family].append((weight, style, name))
    lines = ["", "  <!-- FontRoutes Open: additive named families only -->"]
    for family, fonts in sorted(families.items()):
        lines.append(f'  <family name="{family}">')
        for weight, style, name in sorted(fonts):
            lines.append(f'    <font weight="{weight}" style="{style}">{name}</font>')
        lines.append("  </family>")
    lines.append("")
    inserted = "\n".join(lines).encode("ascii")
    output = xml[:at] + inserted + xml[at:]
    _, new_names, new_count = inspect_xml(output)
    require(new_names == names | set(families) and new_count == count + len(families), "XML post-check failed")
    return output


def installer(product, api, fingerprint_hash, xml_sha, filenames):
    safe_name(product, SAFE_FILE)
    safe_name(fingerprint_hash, SHA)
    safe_name(xml_sha, SHA)
    files = " ".join(safe_name(name, SAFE_FILE) for name in sorted(filenames))
    return f'''#!/system/bin/sh
# install-time check; runs in the module manager, no direct writes to system.
[ "$BOOTMODE" = true ] && [ "$KSU" = true ] || abort "Use KernelSU Manager; other root managers and recovery are not supported."
command -v sha256sum >/dev/null 2>&1 || abort "sha256sum is required."
[ "$(getprop ro.product.device)" = '{product}' ] || abort "Wrong device."
[ "$(getprop ro.build.version.sdk)" = '{api}' ] || abort "Wrong Android SDK."
fingerprint="$(getprop ro.build.fingerprint)"
[ -n "$fingerprint" ] || abort "Build fingerprint unavailable."
actual="$(printf '%s' "$fingerprint" | sha256sum)" || abort "Unable to hash fingerprint."
[ "${{actual%% *}}" = '{fingerprint_hash}' ] || abort "ROM build changed."
[ -r '{ROM_PATH}' ] || abort "ROM font_fallback.xml unavailable."
actual="$(sha256sum '{ROM_PATH}')" || abort "Unable to hash ROM XML."
[ "${{actual%% *}}" = '{xml_sha}' ] || abort "ROM XML mismatch; disable competing modules and rebuild."
for name in {files}; do
  [ ! -e "/system/fonts/$name" ] || abort "A FontRoutes font is already mounted; disable it and reboot before installing."
done
[ -s "$MODPATH/system/etc/font_fallback.xml" ] || abort "Staged font XML missing."
ui_print "FontRoutes Open: additive named font families only."
ui_print "Disable this module before an OTA; verify app selection after reboot."
'''.encode()


def public_profile(args, base, fonts):
    # An input archive is deliberately not a root-module archive.
    base["README.txt"] = b"Public OFL font inputs. This ZIP is not flashable. See the builder README for local ROM assembly.\n"
    base.update({f"fonts/{name}": data for name, data in fonts.items()})
    pack(args.out / f"FontRoutes-Open-{args.profile}-inputs-{VERSION}.zip", base)


def local_profile(args, base, rows, fonts):
    require(args.i_accept_local_rom_overlay, "Local ROM overlay requires explicit opt-in")
    devices, versions = DEVICES[args.profile]
    require(args.product in devices and args.sdk in versions, "Device/SDK not approved for this profile")
    fingerprint = args.fingerprint_file.read_bytes().strip()
    require(len(fingerprint) >= 12 and len(fingerprint) <= 512 and re.fullmatch(rb"[!-~]+", fingerprint), "Invalid ROM fingerprint input")
    require(args.rom_xml.stat().st_size <= MAX_XML, "Input XML too large")
    raw = args.rom_xml.read_bytes()
    expected_hash = safe_name(args.rom_xml_sha256, SHA)
    require(digest(raw) == expected_hash, "Local ROM XML SHA-256 mismatch")
    base["system/etc/font_fallback.xml"] = add_families(raw, rows)
    base.update({f"system/fonts/{name}": data for name, data in fonts.items()})
    base["customize.sh"] = installer(args.product, args.sdk, digest(fingerprint), expected_hash, fonts)
    identifier = f"fontroutes-open-{args.profile}"
    base["module.prop"] = (
        f"id={identifier}\nname=FontRoutes Open ({args.profile}, local ROM only)\n"
        f"version={VERSION}\nversionCode=100\nauthor=FontRoutes Open contributors\n"
        "description=Adds opt-in named OFL font families; preserves OEM defaults. Disable before OTA.\n"
    ).encode()
    base["Builder-MIT-LICENSE.txt"] = (HERE / "LICENSE").read_bytes()
    base["LOCAL-ROM-DO-NOT-UPLOAD.txt"] = (
        b"This build contains OEM ROM-derived configuration and is for local testing only.\n"
        b"Disable conflicting font modules and this module before OTA. Keep backup and recovery access.\n"
    )
    pack(args.out / f"FontRoutes-Open-{args.profile}-LOCAL-DO-NOT-SHARE-{VERSION}.zip", base)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("inputs", "local-module"))
    parser.add_argument("--profile", choices=DEVICES, required=True)
    parser.add_argument("--cache", type=Path, default=HERE / ".cache")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--inputs-zip", type=Path, help="Selected public inputs ZIP for offline local assembly")
    parser.add_argument("--rom-xml", type=Path)
    parser.add_argument("--rom-xml-sha256")
    parser.add_argument("--product")
    parser.add_argument("--sdk", type=int)
    parser.add_argument("--fingerprint-file", type=Path)
    parser.add_argument("--i-accept-local-rom-overlay", action="store_true")
    args = parser.parse_args()
    args.out = args.out or HERE / ("dist" if args.mode == "inputs" else "local-dist")
    if args.mode == "inputs":
        require(not any((args.rom_xml, args.rom_xml_sha256, args.product, args.sdk, args.fingerprint_file, args.i_accept_local_rom_overlay, args.inputs_zip)), "Public inputs reject all ROM/device arguments")
    else:
        require(all((args.rom_xml, args.rom_xml_sha256, args.product, args.sdk, args.fingerprint_file, args.i_accept_local_rom_overlay)), "Local module needs complete device arguments and explicit opt-in")
        require(not args.out.resolve().is_relative_to((HERE / "dist").resolve()), "Local modules cannot be written to CI's public dist directory")
    if args.inputs_zip:
        with tempfile.TemporaryDirectory() as stage:
            cache = Path(stage)
            checked_input_zip(args.inputs_zip, args.profile, cache)
            rows, fonts, licenses, license_specs = sources(args.profile, cache, True)
    else:
        rows, fonts, licenses, license_specs = sources(args.profile, args.cache, args.offline)
    base = {f"licenses/{name}": value for name, value in licenses.items()}
    base["SOURCES.json"] = encode_json({"project": "FontRoutes Open", "profile": args.profile, "version": VERSION, "fonts": rows, "licenses": license_specs})
    if args.mode == "inputs":
        public_profile(args, base, fonts)
    else:
        local_profile(args, base, rows, fonts)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, expat.ExpatError, zipfile.BadZipFile) as error:
        sys.exit(f"Build rejected: {error}")
