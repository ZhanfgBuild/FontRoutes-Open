#!/usr/bin/env python3
"""Produce a licensed Unicode 18 supplement and a reproducible delta audit."""
import argparse
from collections import Counter, defaultdict
import hashlib
import io
import json
from pathlib import Path
import re
import stat
import tarfile
import urllib.error
import urllib.request
import time
import zipfile

import build

HERE = Path(__file__).resolve().parent
GRAPHIC_EXEMPT = {"Cc", "Cf", "Cs", "Zl", "Zp", "Co", "Cn"}
UCD_FILES = {
    "https://www.unicode.org/Public/18.0.0/ucd/DerivedAge.txt",
    "https://www.unicode.org/Public/18.0.0/ucd/UnicodeData.txt",
    "https://www.unicode.org/Public/18.0.0/ucd/Blocks.txt",
    "https://www.unicode.org/license.txt",
}
REPORT_FILE = "UNICODE18-AGE-DELTA-AUDIT.json"
UNIFONT_PREFIX = "https://unifoundry.com/pub/unifont/unifont-18.0.01/"
UNIFONT_FILES = {
    UNIFONT_PREFIX + "font-builds/unifont-18.0.01.otf",
    UNIFONT_PREFIX + "font-builds/unifont_upper-18.0.01.otf",
    UNIFONT_PREFIX + "unifont-18.0.01.tar.gz",
}


def lock():
    return json.loads((HERE / "unicode18.lock.json").read_text("utf-8"))


def pinned_download(spec, cache, offline, allowed, maximum):
    sha = build.safe_name(spec["sha256"], build.SHA)
    url = spec["url"]
    build.require(url in allowed, "Unexpected data/source URL")
    local = cache / sha
    if spec.get("vendored") == "licenses/UNICODE-DATA-LICENSE.txt":
        data = (HERE / spec["vendored"]).read_bytes()
        build.require(0 < len(data) <= maximum and build.digest(data) == sha,
                      "Vendored Unicode license digest mismatch")
        return data
    if local.exists():
        build.require(0 < local.stat().st_size <= maximum, "Invalid cached data size")
        data = local.read_bytes()
    else:
        build.require(not offline, "Data absent from offline cache: " + sha)
        mirrors = spec.get("mirrors", [])
        for mirror in mirrors:
            build.require(
                (build.approved_asset_url(mirror) and mirror.startswith(
                    "https://raw.githubusercontent.com/unicode-org/unicodetools/"))
                or any(mirror == origin.replace("www.", "") for origin in UCD_FILES),
                "Unexpected pinned download mirror",
            )
        candidates = [*mirrors[:1], url, *mirrors[1:]] if mirrors else [url, url, url]
        errors = []
        for index, candidate in enumerate(candidates):
            try:
                with urllib.request.urlopen(candidate, timeout=28) as f:
                    build.require(f.geturl() == candidate, "Unexpected data redirect")
                    data = f.read(maximum + 1)
                build.require(0 < len(data) <= maximum and build.digest(data) == sha, "Data digest mismatch")
                break
            except (OSError, ValueError) as error:
                errors.append(f"{candidate}: {error}")
                if index + 1 < len(candidates):
                    time.sleep(index + 1)
        else:
            raise ValueError("Pinned source unavailable: " + "; ".join(errors))
    build.require(0 < len(data) <= maximum and build.digest(data) == sha,
                  "Data digest mismatch: " + url)
    if not local.exists():
        build.store_checked(data, sha, cache)
    return data


def parse_ranges(data):
    for line in data.decode("utf-8").splitlines():
        text = line.partition("#")[0].strip()
        if not text:
            continue
        interval, value = (s.strip() for s in text.split(";"))
        endpoints = interval.split("..")
        build.require(len(endpoints) in (1, 2), "Malformed UCD range")
        lo, hi = int(endpoints[0], 16), int(endpoints[-1], 16)
        build.require(0 <= lo <= hi <= 0x10FFFF, "Invalid UCD range")
        yield lo, hi, value


def age_delta(data, expected):
    seen = set()
    for lo, hi, value in parse_ranges(data):
        if value != "18.0":
            continue
        current = set(range(lo, hi + 1))
        build.require(not seen.intersection(current), "Overlapping Unicode 18 age ranges")
        seen.update(current)
    build.require(len(seen) == expected, f"Unicode 18 snapshot has {len(seen)} targets, expected {expected}")
    return seen


def categories(data, wanted):
    known = {}
    pending = None
    for row in data.decode("utf-8").splitlines():
        fields = row.split(";")
        if len(fields) < 3:
            continue
        cp, name, category = int(fields[0], 16), fields[1], fields[2]
        if name.endswith(", First>"):
            build.require(pending is None, "Malformed UnicodeData range")
            pending = cp, category
        elif name.endswith(", Last>"):
            build.require(pending is not None and pending[1] == category, "Unpaired UnicodeData range")
            for value in wanted.intersection(range(pending[0], cp + 1)):
                known[value] = category
            pending = None
        elif cp in wanted:
            known[cp] = category
    build.require(pending is None and wanted == set(known), "Missing UnicodeData categories for age targets")
    return known


def outline_scan(payload, wanted):
    """Conservative static inspection, never claim visual correctness or shaping."""
    from fontTools.pens.recordingPen import DecomposingRecordingPen
    from fontTools.ttLib import TTFont

    mapped, strong, weak, font_stats = defaultdict(list), defaultdict(list), defaultdict(list), {}
    for filename, data in sorted(payload.items()):
        with TTFont(io.BytesIO(data), recalcTimestamp=False) as f:
            cmap = f.getBestCmap() or {}
            glyphs = f.getGlyphSet()
            outlines = {}
            reasons = Counter()
            blocked = {".notdef", "null", "nonmarkingreturn"}
            placeholder_codes = (0xFFFD, 0x25A1, 0x25A0, 0x2610)

            def shape(name):
                if name in outlines:
                    return outlines[name]
                if name not in glyphs or name in {"null", "nonmarkingreturn"}:
                    result = None
                else:
                    pen = DecomposingRecordingPen(glyphs)
                    try:
                        glyphs[name].draw(pen)
                        visible = any(op in {"lineTo", "curveTo", "qCurveTo"} for op, _ in pen.value)
                        result = hashlib.sha256(repr(pen.value).encode()).hexdigest() if visible else None
                    except (KeyError, ValueError, TypeError, RecursionError):
                        result = None
                outlines[name] = result
                return result

            tofu = {shape(n) for n in (".notdef", *(cmap.get(cp) for cp in placeholder_codes)) if n}
            matched = wanted.intersection(cmap)
            candidates = {}
            duplicates = Counter()
            for cp in sorted(matched):
                name = cmap[cp]
                mapped[cp].append(filename)
                val = shape(name)
                if val is None or val in tofu or name in blocked:
                    weak[cp].append({"font": filename, "reason": "empty, unreadable, or known placeholder outline"})
                    reasons["empty_or_placeholder"] += 1
                    continue
                candidates[cp] = val
                duplicates[val] += 1
            for cp, val in candidates.items():
                if duplicates[val] >= 8:
                    weak[cp].append({"font": filename, "reason": "identical outline reused by at least 8 new targets"})
                    reasons["high_duplicate_outline"] += 1
                else:
                    strong[cp].append(filename)
            font_stats[filename] = {
                "mapped": len(matched), "accepted_outline": len(candidates) - reasons["high_duplicate_outline"],
                "rejected": dict(reasons), "sha256": build.digest(data),
            }
    return mapped, strong, weak, font_stats


def audit(spec, data, baseline, supplement):
    expected = spec["expected_delta"]
    wanted = age_delta(data["DerivedAge.txt"], expected)
    general = categories(data["UnicodeData.txt"], wanted)
    block_values = {}
    for lo, hi, label in parse_ranges(data["Blocks.txt"]):
        for cp in wanted.intersection(range(lo, hi + 1)):
            block_values[cp] = label
    mapped, strong, weak, fonts = outline_scan({**baseline, **supplement}, wanted)
    for row in spec.get("fonts", []):
        if "expected_outline_count" not in row:
            continue
        name = row["filename"]
        expected_block = row["expected_outline_block"]
        count = sum(name in providers for point, providers in strong.items()
                    if block_values.get(point) == expected_block)
        build.require(count == row["expected_outline_count"],
                      f"{name}: {expected_block} outline count {count}, expected {row['expected_outline_count']}")
    cohorts = defaultdict(Counter)
    entries = []
    all_counts = Counter()
    provisional = {row["filename"] for row in spec.get("fonts", []) if row.get("quality") == "provisional"}
    eligible = set(baseline) | {row["filename"] for row in spec.get("fonts", []) if row.get("mount", True)}
    base_count = sum(any(name in baseline for name in strong.get(cp, ())) for cp in wanted if general[cp] not in GRAPHIC_EXEMPT)
    named_count = sum(any(name in eligible for name in strong.get(cp, ())) for cp in wanted if general[cp] not in GRAPHIC_EXEMPT)
    for cp in sorted(wanted):
        cat = general[cp]
        label = block_values.get(cp, "No Unicode block")
        if cat in GRAPHIC_EXEMPT:
            status = "non_graphic"
        elif strong.get(cp):
            status = "outline"
        elif mapped.get(cp):
            status = "mapped_without_accepted_outline"
        else:
            status = "missing"
        all_counts[status] += 1
        cohorts[label][status] += 1
        tentative = [name for name in strong.get(cp, []) if name in provisional]
        if status == "outline" and tentative and not set(strong[cp]).difference(provisional):
            all_counts["provisional_only"] += 1
        entries.append({
            "codepoint": f"U+{cp:04X}", "block": label, "category": cat, "status": status,
            **({"accepted_providers": strong[cp]} if strong.get(cp) else {}),
            **({"provisional_providers": tentative} if tentative else {}),
            **({"rejected_providers": weak[cp]} if weak.get(cp) else {}),
        })
    return {
        "scope": "Code points newly assigned in Unicode 18.0, Age == 18.0; licensed font bytes only",
        "data": spec["data"], "data_attribution": "Unicode Character Database © Unicode, Inc.; Unicode License v3",
        "limitations": [
            "Coverage counts nonempty, non-placeholder outlines in best cmap; not script shaping or visual certification.",
            "No font routing, older Unicode repertoire, variation sequences, RGI emoji, color glyphs or device behavior verified.",
            "High-duplicate outlines are conservatively excluded. Standalone Emoji and Brands libraries are not in the profile.",
        ],
        "expected_age_delta": expected, "graphic_target": expected - all_counts["non_graphic"],
        "baseline_accepted_graphic_outlines": base_count,
        "named_eligible_accepted_graphic_outlines": named_count,
        "provisional_only_accepted_graphic_outlines": all_counts["provisional_only"],
        "counts": {name: all_counts[name] for name in ("outline", "missing", "mapped_without_accepted_outline", "non_graphic")},
        "blocks": {name: dict(counts) for name, counts in sorted(cohorts.items())},
        "fonts": fonts, "entries": entries,
    }


def unifont_license(spec, cache, offline):
    pack = spec["unifont_license_tar"]
    raw = pinned_download(pack, cache, offline, UNIFONT_FILES, 100 * 1024 * 1024)
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as source:
        members = [m for m in source if m.name == pack["member"]]
        build.require(len(members) == 1 and members[0].isfile() and 0 < members[0].size <= 32768,
                      "Missing or unsafe exact Unifont release license")
        with source.extractfile(members[0]) as file:
            data = file.read()
    build.require(build.digest(data) == pack["license_sha256"], "Unifont release license digest mismatch")
    return data


def supplement_assets(spec, cache, offline):
    rows, fonts, notices, provenance = spec["fonts"], {}, {}, {}
    for row in rows:
        name = build.safe_name(row["filename"], build.SAFE_FILE)
        license_name = build.safe_name(row["license"], build.SAFE_FILE)
        build.require(name not in fonts, "Duplicate supplement font path")
        if row.get("asset_type") == "kaiyuan-reviewed":
            import reviewed_seal
            source = spec["kaiyuan_reviewed_source"]
            archive = pinned_download(source, cache, offline, {reviewed_seal.URL}, 40_000_001)
            fonts[name], derived_notices, approval = reviewed_seal.make_font(archive)
            build.require(build.digest(fonts[name]) == row["sha256"], "Reviewed Seal deterministic font digest changed")
            build.require(build.digest(approval) == source["approval_file_sha256"],
                          "Reviewed Seal approval digest changed")
            provenance["Kaiyuan-approved.csv"] = approval
            for extra, digest in source["upstream_notices"].items():
                build.require(build.digest(derived_notices[extra]) == digest, "Reviewed Seal upstream notice digest changed")
            notices.update(derived_notices)
        elif row.get("asset_type") == "embedded-ofl":
            from fontTools.ttLib import TTFont
            font_source = {"url": row["url"], "sha256": row["sha256"]}
            fonts[name] = build.fetch_asset(font_source, cache, offline)
            bounds = [int(point, 16) for point in row["exact_cmap_range"]]
            expected = set(range(bounds[0], bounds[1] + 1))
            with TTFont(io.BytesIO(fonts[name]), recalcTimestamp=False) as source:
                cmap = source.getBestCmap()
                build.require(set(cmap) == expected and len(set(cmap.values())) == len(expected),
                              "Archaic Numerals cmap is not the exact 311 assigned code points")
                names = source["name"]
                copyright_text, license_text = names.getDebugName(0), names.getDebugName(13)
                build.require(names.getDebugName(1) == row["embedded_family"] and copyright_text and license_text,
                              "Missing expected original family or embedded OFL metadata")
                build.require(all(set(t.cmap) <= expected for t in source["cmap"].tables if t.isUnicode()),
                              "Archaic Numerals has unexpected extra Unicode mappings")
                notices[license_name] = (copyright_text.strip() + "\n\n" + license_text.strip() + "\n").encode("utf-8")
        elif row.get("asset_type") == "tangut-ids-trial":
            from tangut_derived import derive
            donor = build.fetch_asset(row["sources"]["base_font"], cache, offline)
            notices[license_name] = build.fetch_asset(row["sources"]["license"], cache, offline)
            fonts[name] = derive(donor, notices[license_name])
            build.require(build.digest(fonts[name]) == row["sha256"], "Provisional Tangut font digest changed")
        elif row.get("asset_type") == "unifont":
            fonts[name] = pinned_download(row, cache, offline, UNIFONT_FILES, build.MAX_ASSET)
            if license_name not in notices:
                notices[license_name] = unifont_license(spec, cache, offline)
        else:
            fonts[name] = build.fetch_asset(row, cache, offline)
            notices[license_name] = build.fetch_asset({"url": row["license_url"], "sha256": row["license_sha256"]}, cache, offline)
        build.require(build.digest(notices[license_name]) == row["license_sha256"], "Supplement license mismatch")
        build.require(b"SIL OPEN FONT LICENSE" in notices[license_name].upper(), "Supplement license is not OFL")
    return fonts, notices, provenance


def load_module_inputs(package):
    spec = lock()
    rows = spec["fonts"]
    require_paths = {f"fonts/{row['filename']}": row["sha256"] for row in rows}
    require_paths.update({f"licenses/{row['license']}": row["license_sha256"] for row in rows})
    additional_notices = spec["kaiyuan_reviewed_source"]["upstream_notices"]
    require_paths.update({f"licenses/{name}": sha for name, sha in additional_notices.items()})
    extra = {"README.txt", "SOURCES.json", REPORT_FILE, "licenses/LICENSE-UNICODE.txt", "provenance/Kaiyuan-approved.csv"}
    build.require(package.stat().st_size <= 2 * build.MAX_ASSET, "Supplement archive too large")
    with zipfile.ZipFile(package) as z:
        infos = z.infolist()
        build.require(len(infos) == len(require_paths) + len(extra)
                      and {i.filename for i in infos} == set(require_paths) | extra, "Unexpected supplement archive members")
        fonts, licenses = {}, {}
        for name, sha in require_paths.items():
            info = z.getinfo(name)
            build.require(0 < info.file_size <= build.MAX_ASSET and not info.flag_bits & 1
                          and stat.S_ISREG(info.external_attr >> 16), "Unsafe supplement asset")
            data = z.read(info)
            build.require(build.digest(data) == sha, "Supplement input hash mismatch")
            folder, leaf = name.split("/")
            (fonts if folder == "fonts" else licenses)[leaf] = data
    local_rows = [row for row in rows if row.get("mount", True)]
    return (local_rows,
            {row["filename"]: fonts[row["filename"]] for row in local_rows},
            {name: licenses[name] for name in {row["license"] for row in local_rows} | set(additional_notices)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=HERE / ".cache")
    parser.add_argument("--out", type=Path, default=HERE / "dist")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--seal-reference", action="store_true",
                        help="Audit pinned AGPL Seal Sans as a local reference only; no ZIP or mount")
    parser.add_argument("--require-complete", action="store_true",
                        help="Fail unless every graphic Age=18.0 target has an accepted outline")
    args = parser.parse_args()
    spec = lock()
    data = {name: pinned_download(entry, args.cache, args.offline, UCD_FILES, 5 * 1024 * 1024) for name, entry in spec["data"].items()}
    _, baseline, _, _ = build.sources("op13-full", args.cache, args.offline)
    supplement, notices, provenance = supplement_assets(spec, args.cache, args.offline)
    report = audit(spec, data, baseline, supplement)
    if args.seal_reference:
        ref = spec["seal_reference_only"]
        ref_font = build.fetch_asset(ref, args.cache, args.offline)
        ref_license = build.fetch_asset({"url": ref["license_url"], "sha256": ref["license_sha256"]},
                                        args.cache, args.offline)
        build.require(b"GNU AFFERO GENERAL PUBLIC LICENSE" in ref_license.upper(),
                      "Unexpected Seal reference license")
        seal_targets = {int(row["codepoint"][2:], 16) for row in report["entries"] if row["block"] == "Seal"}
        build.require(len(seal_targets) == 11328, "Unexpected Seal delta target count")
        _, strong, _, _ = outline_scan({ref["filename"]: ref_font}, seal_targets)
        print(json.dumps({
            "reference_only": True, "license": ref["license"], "source_url": ref["source_url"],
            "scope": "Small Seal Age=18.0 code points; nonempty, non-placeholder static outline scan only",
            "target": len(seal_targets), "accepted_outline": len(strong),
            "default_public_report_unchanged": True,
            "limitations": "Glyph shapes and provenance are not approved for bundling. No ZIP, routing or mount.",
        }, sort_keys=True))
        return 2 if args.require_complete and len(strong) != len(seal_targets) else 0
    print(json.dumps({k: report[k] for k in ("expected_age_delta", "graphic_target", "baseline_accepted_graphic_outlines", "named_eligible_accepted_graphic_outlines", "provisional_only_accepted_graphic_outlines", "counts")}, sort_keys=True))
    name = f"FontRoutes-Open-unicode18-supplement-{build.VERSION}.zip"
    build.pack(args.out / name, {
        **{f"fonts/{filename}": value for filename, value in supplement.items()},
        **{f"licenses/{filename}": value for filename, value in notices.items()},
        **{f"provenance/{filename}": value for filename, value in provenance.items()},
        "licenses/LICENSE-UNICODE.txt": data["LICENSE-UNICODE.txt"],
        REPORT_FILE: build.encode_json(report),
        "SOURCES.json": build.encode_json(spec),
        "README.txt": (
            b"Optional public Unicode 18 inputs, not flashable. Contains static Age=18.0 audit against OP13 Full + supplement. "
            b"Missing targets remain explicit. No global routing, complete repertoire or device shaping claim.\n"
        ),
    })
    missing = report["counts"]["missing"] + report["counts"]["mapped_without_accepted_outline"]
    if args.require_complete and missing:
        return 2
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, zipfile.BadZipFile) as error:
        raise SystemExit(f"Unicode 18 audit rejected: {error}") from error
