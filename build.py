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
import urllib.parse
import urllib.request
import zipfile
from collections import defaultdict
from pathlib import Path
from xml.parsers import expat

from logo_fallback import LOGO_OUT, POSTSCRIPT as LOGO_PS

HERE = Path(__file__).resolve().parent
# Read the literal model or codename returned by ro.product.device, not by guessing the market.
DEVICES = {
    "op13-full": ({"PJZ110", "CPH2649", "CPH2653", "CPH2655", "OP5D0DL1", "dodge", "OnePlus13"}, {35, 36, 37}),
    "op6-slim": ({"A6000", "A6003", "enchilada", "OnePlus6"}, {37}),
}
VERSION = "0.2.0"
MAX_ASSET = 40 * 1024 * 1024
MAX_RELEASE_ARCHIVE = 130 * 1024 * 1024
MAX_XML = 2 * 1024 * 1024
ROM_PATH = "/system/etc/font_fallback.xml"
ROM_CONFIGS = {"font_fallback.xml": ROM_PATH, "fonts.xml": "/system/etc/fonts.xml"}
MOUNT_BACKENDS = ("ksu-metamodule", "ksu-legacy-unverified")
META_MOUNT_MARKER = "/data/adb/metamodule/metamount.sh"
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


def approved_release_url(url):
    """Official asset URL with checked-in digest; a tag alone is not a content pin."""
    return bool(re.fullmatch(
        r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/releases/download/[A-Za-z0-9_.+-]+/[A-Za-z0-9_.+-]+",
        url,
    )) and not {".", ".."}.intersection(url.split("/"))


def store_checked(data, sha, cache):
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / sha
    with tempfile.NamedTemporaryFile(dir=cache, delete=False) as handle:
        handle.write(data)
        tmp = handle.name
    os.replace(tmp, target)


def fetch_verified(url, sha, cache, offline, maximum, release=False):
    sha = safe_name(sha, SHA)
    require(approved_release_url(url) if release else approved_asset_url(url), "Source URL is not an allowed official pin")
    target = cache / sha
    if target.exists():
        require(0 < target.stat().st_size <= maximum, "Cached input too large or empty")
        data = target.read_bytes()
        require(digest(data) == sha, f"Cached input SHA-256 mismatch: {sha}")
        return data
    require(not offline, f"Offline source is missing: {sha}")
    with urllib.request.urlopen(url, timeout=90) as response:
        endpoint = urllib.parse.urlsplit(response.geturl())
        require(response.geturl() == url or (
            release and endpoint.scheme == "https" and endpoint.netloc in
            {"release-assets.githubusercontent.com", "objects.githubusercontent.com"}
        ), "Unexpected asset redirect")
        data = response.read(maximum + 1)
    require(0 < len(data) <= maximum and digest(data) == sha, f"Source SHA-256 mismatch: {url}")
    store_checked(data, sha, cache)
    return data


def fetch_asset(spec, cache, offline=False):
    sha = safe_name(spec["sha256"], SHA)
    if "member" not in spec:
        if "asset_type" not in spec or spec["asset_type"] == "raw":
            return fetch_verified(spec["url"], sha, cache, offline, MAX_ASSET)
        require(spec["asset_type"] == "release", "Invalid asset type")
        return fetch_verified(spec["url"], sha, cache, offline, MAX_ASSET, release=True)
    require(approved_release_url(spec["url"]), "Source release URL is not an allowed official pin")
    require(all(re.fullmatch(SAFE_FILE, s) for s in spec["member"].split("/")), "Unsafe release member")
    archive_sha = safe_name(spec["archive_sha256"], SHA)
    target = cache / sha
    if target.exists():
        require(0 < target.stat().st_size <= MAX_ASSET, "Cached member too large or empty")
        cached = target.read_bytes()
        require(digest(cached) == sha, f"Cached input SHA-256 mismatch: {sha}")
        return cached
    archive = fetch_verified(spec["url"], archive_sha, cache, offline, MAX_RELEASE_ARCHIVE, release=True)
    with zipfile.ZipFile(io.BytesIO(archive)) as z:
        matching = [f for f in z.infolist() if f.filename == spec["member"]]
        require(len(matching) == 1 and 0 < matching[0].file_size <= MAX_ASSET and
                not matching[0].flag_bits & 1 and not matching[0].is_dir(),
                "Expected exactly one bounded, unencrypted release member")
        data = z.read(matching[0])
    require(digest(data) == sha, f"Release member SHA-256 mismatch: {spec['member']}")
    store_checked(data, sha, cache)
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


def add_families(xml, rows, include_logo=False):
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
    if include_logo:
        require(LOGO_OUT.encode() not in xml, "ROM already references the local compatibility font; disable the old module")
        lines.extend([
            "  <!-- Local, appended fallback: one U+F8FF glyph, no OEM family reordering -->",
            "  <family>",
            f'    <font weight="400" style="normal" postScriptName="{LOGO_PS}">{LOGO_OUT}</font>',
            "  </family>",
        ])
    lines.append("")
    inserted = "\n".join(lines).encode("ascii")
    output = xml[:at] + inserted + xml[at:]
    _, new_names, new_count = inspect_xml(output)
    require(new_names == names | set(families) and new_count == count + len(families) + int(include_logo), "XML post-check failed")
    return output


def installer(product, api, fingerprint_hash, xml_sha, filenames,
              rom_config="font_fallback.xml", mount_backend="ksu-metamodule"):
    require(rom_config in ROM_CONFIGS, "Unsupported ROM config path")
    require(mount_backend in MOUNT_BACKENDS, "Unsupported mount backend")
    require(type(api) is int and 35 <= api <= 37, "Unsupported Android SDK")
    rom_path = ROM_CONFIGS[rom_config]
    backend_guard = (
        f'[ -f "{META_MOUNT_MARKER}" ] && [ -r "{META_MOUNT_MARKER}" ] && '
        f'[ ! -e "{Path(META_MOUNT_MARKER).parent}/disable" ] && [ ! -e "{Path(META_MOUNT_MARKER).parent}/remove" ] || '
        'abort "KernelSU system mount provider missing. Verify your installed metamodule, then retry."'
        if mount_backend == "ksu-metamodule" else
        'ui_print "Legacy KernelSU mount backend explicitly selected; no metamodule is verified."'
    )
    preference_guard = (
        f'[ ! -e "{ROM_PATH}" ] && [ ! -L "{ROM_PATH}" ] || '
        'abort "font_fallback.xml exists; refusing legacy fonts.xml overlay."'
        if rom_config == "fonts.xml" else ':'
    )
    safe_name(product, SAFE_FILE)
    safe_name(fingerprint_hash, SHA)
    safe_name(xml_sha, SHA)
    files = " ".join(safe_name(name, SAFE_FILE) for name in sorted(filenames))
    return f'''#!/system/bin/sh
# install-time check; runs in the module manager, no direct writes to system.
[ "$BOOTMODE" = true ] && [ "$KSU" = true ] || abort "Use KernelSU Manager; other root managers and recovery are not supported."
command -v sha256sum >/dev/null 2>&1 || abort "sha256sum is required."
{backend_guard}
{preference_guard}
[ "$(getprop ro.product.device)" = '{product}' ] || abort "Wrong device."
[ "$(getprop ro.build.version.sdk)" = '{api}' ] || abort "Wrong Android SDK."
fingerprint="$(getprop ro.build.fingerprint)"
[ -n "$fingerprint" ] || abort "Build fingerprint unavailable."
actual="$(printf '%s' "$fingerprint" | sha256sum)" || abort "Unable to hash fingerprint."
[ "${{actual%% *}}" = '{fingerprint_hash}' ] || abort "ROM build changed."
[ -r '{rom_path}' ] || abort "Selected ROM font configuration unavailable."
actual="$(sha256sum '{rom_path}')" || abort "Unable to hash ROM XML."
[ "${{actual%% *}}" = '{xml_sha}' ] || abort "ROM XML mismatch; disable competing modules and rebuild."
for name in {files}; do
  [ ! -e "/system/fonts/$name" ] && [ ! -L "/system/fonts/$name" ] || abort "A FontRoutes font is already mounted; disable it and reboot before installing."
done
[ -s "$MODPATH/system/etc/{rom_config}" ] || abort "Staged font XML missing."
for name in {files}; do
  [ -s "$MODPATH/system/fonts/$name" ] || abort "Staged font payload missing."
done
[ -s "$MODPATH/STAGING-SHA256SUMS.txt" ] || abort "Staged checksum manifest missing."
(cd "$MODPATH" && sha256sum -c STAGING-SHA256SUMS.txt >/dev/null 2>&1) || abort "Staged file integrity check failed."
ui_print "FontRoutes Open: additive named font families only."
ui_print "Staged only. Reboot, then verify the file mounts and app selection."
ui_print "Disable this module and reboot before an OTA or ROM change."
'''.encode()


def public_profile(args, base, fonts):
    # An input archive is deliberately not a root-module archive.
    base["README.txt"] = b"Public OFL font inputs. This ZIP is not flashable. See the builder README for local ROM assembly.\n"
    base.update({f"fonts/{name}": data for name, data in fonts.items()})
    pack(args.out / f"FontRoutes-Open-{args.profile}-inputs-{VERSION}.zip", base)


def validate_rom(args, rows):
    """Same offline gates for doctor and assembly; never print/copy raw device identity."""
    devices, versions = DEVICES[args.profile]
    require(args.product in devices, "Device is outside this profile's allowlist")
    require(args.sdk in versions, "Android SDK is outside this profile's allowlist")
    require(args.sdk != 35 or args.confirm_api35_active_config,
            "Android 15 may choose its parser via a feature flag; explicitly confirm the actual config path")
    require(args.rom_config != "fonts.xml" or args.confirm_legacy_rom_path,
            "Legacy fonts.xml requires explicit attestation; use font_fallback.xml if present")
    require(args.fingerprint_file.stat().st_size <= 1024, "ROM fingerprint input too large")
    fingerprint = args.fingerprint_file.read_bytes().strip()
    require(12 <= len(fingerprint) <= 512 and re.fullmatch(rb"[!-~]+", fingerprint),
            "Invalid ROM fingerprint input")
    require(args.rom_xml.stat().st_size <= MAX_XML, "Input XML too large")
    raw = args.rom_xml.read_bytes()
    expected = safe_name(args.rom_xml_sha256, SHA)
    require(digest(raw) == expected, "Local ROM XML SHA-256 mismatch")
    draft_xml = add_families(raw, rows, include_logo=not args.no_logo_fallback)
    return draft_xml, digest(fingerprint), expected


def doctor(args):
    """Check a supplied snapshot and optional input ZIP; never ADB, mount, or assemble."""
    rows, _ = profile_lock(args.profile)
    validate_rom(args, rows)
    if args.inputs_zip:
        with tempfile.TemporaryDirectory() as stage:
            cache = Path(stage)
            checked_input_zip(args.inputs_zip, args.profile, cache)
            sources(args.profile, cache, True)
    if args.logos_zip:
        get_library_inputs("logos", args.cache, True, args.logos_zip)
    print(encode_json({
        "status": "offline-preflight-passed",
        "profile": args.profile,
        "sdk": args.sdk,
        "rom_config": ROM_CONFIGS[args.rom_config],
        "input_zip": "locked fonts/licenses verified" if args.inputs_zip else "not supplied; not verified",
        "kernel_su_backend": args.mount_backend,
        "logo_fallback": ("disabled" if args.no_logo_fallback else
                          "one F8FF glyph appended after OEM fallback; runtime selection unverified"),
        "mount_status": "unverified; reboot and inspect on the phone",
        "checks": [
            "device and SDK allowlists",
            "matching ROM XML digest",
            "safe byte-preserving additive named-family transform",
            "no alias, fallbackFor or family-name collision",
        ],
        "next_check": ("Verify an enabled KernelSU metamodule and its system mount support before installation."
                       if args.mount_backend == "ksu-metamodule" else
                       "Legacy backend is not validated; compare mounted files after reboot.")
    }).decode(), end="")


def library_spec(kind):
    return json.loads((HERE / "fonts.lock.json").read_text("utf-8"))["libraries"][kind]


def get_library_inputs(kind, cache, offline, package=None):
    spec = library_spec(kind)
    if not package:
        return fetch_asset(spec["font"], cache, offline), fetch_asset(spec["license"], cache, offline)
    require(kind == "logos", "Only the standalone Brands ZIP can supply the local logo source")
    require(package.stat().st_size <= 2 * MAX_ASSET, "Brands input ZIP too large")
    paths = (f"fonts/{safe_name(spec['font']['filename'], SAFE_FILE)}", "licenses/UPSTREAM-LICENSE.txt")
    with zipfile.ZipFile(package) as z:
        entries = z.infolist()
        require(len(entries) == 4 and {info.filename for info in entries} ==
                {*paths, "SOURCES.json", "README.txt"}, "Unexpected Brands ZIP entries")
        checked = []
        for path, item in zip(paths, (spec["font"], spec["license"]), strict=True):
            info = z.getinfo(path)
            require(0 < info.file_size <= MAX_ASSET and not info.flag_bits & 1
                    and stat.S_ISREG(info.external_attr >> 16), "Invalid Brands ZIP member")
            data = z.read(info)
            require(digest(data) == item["sha256"], "Brands ZIP SHA-256 mismatch")
            checked.append(data)
    return tuple(checked)


def public_library(args):
    """Standalone app-use library; excluded from both public profiles and root ZIPs."""
    kind = args.mode
    spec = library_spec(kind)
    font, lic = get_library_inputs(kind, args.cache, args.offline)
    require(font[:4] in (b"OTTO", b"\x00\x01\x00\x00"), "Library asset is not OpenType")
    lic = fetch_asset(spec["license"], args.cache, args.offline)
    require(b"SIL OPEN FONT LICENSE" in lic.upper(), "Library lacks expected OFL")
    name = safe_name(spec["font"]["filename"], SAFE_FILE)
    if kind == "logos":
        require(b"TRADEMARKS" in lic.upper(), "Brand license lacks trademark notice")
        tag = "logos-brand-use"
        notes = (
            b"Standalone app asset, not a module. Unmodified upstream Font Awesome Brands font. "
            b"Font license does not grant third-party trademark rights. Use marks to represent their owners; "
            b"do not imply endorsement. Not installed into system PUA.\n"
        )
    else:
        tag = "emoji-2d-app-use"
        notes = (
            b"Standalone app asset, not a module. Unmodified upstream Noto Emoji 2D. "
            b"No platform font, system emoji or fallback route is replaced.\n"
        )
    pack(args.out / f"FontRoutes-Open-{tag}-{VERSION}.zip", {
        f"fonts/{name}": font,
        "licenses/UPSTREAM-LICENSE.txt": lic,
        "SOURCES.json": encode_json(spec),
        "README.txt": notes,
    })


def local_profile(args, base, rows, fonts):
    require(args.i_accept_local_rom_overlay, "Local ROM overlay requires explicit opt-in")
    xml, fingerprint_digest, expected_hash = validate_rom(args, rows)
    base[f"system/etc/{args.rom_config}"] = xml
    if not args.no_logo_fallback:
        from logo_fallback import derive
        brand, notice = get_library_inputs("logos", args.cache, args.offline, args.logos_zip)
        fonts = {**fonts, LOGO_OUT: derive(brand)}
        base["licenses/Font-Awesome-LICENSE.txt"] = notice
        base["licenses/Compatibility-Mark-MODIFICATIONS.txt"] = (
            b"Unmodified glyph outline from the Apple-representing U+F179 entry in Font Awesome Free 7.3.1 Brands. "
            b"Subset and remapped to U+F8FF only; modified font family/CFF names. SIL OFL 1.1; see upstream notice. "
            b"The underlying mark is a third-party trademark. No trademark license or endorsement is implied. "
            b"Local ROM-bound ZIP only; do not redistribute.\n"
        )
    base.update({f"system/fonts/{name}": data for name, data in fonts.items()})
    system_files = {k: data for k, data in base.items() if k.startswith("system/")}
    base["STAGING-SHA256SUMS.txt"] = "".join(
        f"{digest(data)}  {name}\n" for name, data in sorted(system_files.items())).encode()
    base["MOUNTED-SHA256SUMS.txt"] = "".join(
        f"{digest(data)}  /{name}\n" for name, data in sorted(system_files.items())).encode()
    base["customize.sh"] = installer(args.product, args.sdk, fingerprint_digest, expected_hash, fonts,
                                     args.rom_config, args.mount_backend)
    identifier = f"fontroutes-open-{args.profile}"
    base["module.prop"] = (
        f"id={identifier}\nname=FontRoutes Open ({args.profile}, local ROM only)\n"
        f"version={VERSION}\nversionCode=200\nauthor=FontRoutes Open contributors\n"
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
    parser.add_argument("mode", choices=("inputs", "local-module", "doctor", "logos", "emoji"))
    parser.add_argument("--profile", choices=DEVICES)
    parser.add_argument("--cache", type=Path, default=HERE / ".cache")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--inputs-zip", type=Path, help="Selected public inputs ZIP for offline local assembly")
    parser.add_argument("--logos-zip", type=Path, help="Verified standalone Brands ZIP as an offline local logo source")
    parser.add_argument("--no-logo-fallback", action="store_true", help="Keep OEM font routing exactly as-is for U+F8FF")
    parser.add_argument("--rom-config", choices=ROM_CONFIGS, default="font_fallback.xml")
    parser.add_argument("--confirm-api35-active-config", action="store_true",
                        help="Android 15 uses a runtime flag: independently confirm the selected ROM actually reads this path")
    parser.add_argument("--confirm-legacy-rom-path", action="store_true",
                        help="Locally confirmed ROM actually reads legacy fonts.xml and modern path is absent")
    parser.add_argument("--mount-backend", choices=MOUNT_BACKENDS, default="ksu-metamodule")
    parser.add_argument("--rom-xml", type=Path)
    parser.add_argument("--rom-xml-sha256")
    parser.add_argument("--product")
    parser.add_argument("--sdk", type=int)
    parser.add_argument("--fingerprint-file", type=Path)
    parser.add_argument("--i-accept-local-rom-overlay", action="store_true")
    args = parser.parse_args()
    default_args = (args.rom_config == "font_fallback.xml" and args.mount_backend == "ksu-metamodule"
                    and not args.confirm_legacy_rom_path and not args.confirm_api35_active_config
                    and not args.no_logo_fallback and args.logos_zip is None)
    if args.mode in ("inputs", "logos", "emoji"):
        require(default_args and not any((args.rom_xml, args.rom_xml_sha256, args.product, args.sdk,
                                        args.fingerprint_file, args.i_accept_local_rom_overlay, args.inputs_zip)),
                "Public inputs reject all ROM/device arguments")
        args.out = args.out or HERE / "dist"
        if args.mode in ("logos", "emoji"):
            require(args.profile is None, "Libraries are standalone; omit the device profile")
            public_library(args)
            return
        require(args.profile is not None, "Choose a public input profile")
    else:
        require(args.profile is not None, "Choose a local device profile")
        require(all((args.rom_xml, args.rom_xml_sha256, args.product, args.sdk, args.fingerprint_file)),
                "Local preflight needs complete device arguments")
        require(not (args.no_logo_fallback and args.logos_zip), "Brands ZIP cannot be supplied when logo fallback is disabled")
        if args.mode == "doctor":
            require(args.out is None and not args.i_accept_local_rom_overlay and not args.offline,
                    "Doctor never creates a module or writes output files; omit build-only flags")
            doctor(args)
            return
        require(args.i_accept_local_rom_overlay, "Local module needs explicit opt-in")
        args.out = args.out or HERE / "local-dist"
        require(not args.out.resolve().is_relative_to((HERE / "dist").resolve()),
                "Local modules cannot be written to CI's public dist directory")
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
