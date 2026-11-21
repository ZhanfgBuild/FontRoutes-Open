# FontRoutes Open

Independent, open-input font builder for **OnePlus 13 Full** and **OnePlus 6
Slim** profiles. It does not import source, fonts, releases, ROM files, or Git
history from any private project. Project code is MIT; unmodified font bytes
retain their SIL Open Font License 1.1 and upstream notices. This is not a
vendor product or a drop-in replacement for a private root module.

| Profile | Locked public inputs | Candidate device range |
| --- | --- | --- |
| `op13-full` | Noto Sans 2.015 Regular/Bold, Mono 2.014 Regular, CJK SC Sans2.004 Regular, STIX Two Math 2.13b171, LXGW WenKai 1.530 Regular | Listed OnePlus 13 product identifiers, SDK 35–37 |
| `op6-slim` | Noto Sans 2.015 Regular and CJK SC Sans2.004 Regular | Listed OnePlus 6 product identifiers, SDK 37 |

The CJK Sans pin stays on Sans2.004; the newer release in the CJK repository
is the separate Serif family. Slim uses two faces; the broad CJK face accounts
for most of its size. These allowlists are **gates, not proven compatibility**.
No private Apple/OEM fonts, OEM or community logo assets, boot binaries, custom
default Emoji or forced system-wide fallback are included.

## Build public inputs and optional app libraries

Python 3.11+; building from upstream needs HTTPS.

```sh
python3 -m unittest discover -s tests -v
python3 build.py inputs --profile op13-full
python3 build.py inputs --profile op6-slim
python3 build.py logos   # optional Font Awesome Free Brands 7.3.1
python3 build.py emoji   # optional Noto Emoji 2D, Unicode 18
```

The four commands produce independent ZIPs and SHA-256 sidecars in `dist/`.
**None can be flashed.** Each contains original public font bytes, upstream
notices and a source manifest. GitHub Actions runs all four builds; it uploads
only these inputs and never creates releases. The lock pins raw files to
commits; where upstream ships generated binaries only in release assets, it
pins archive and selected member digests. The two downloads are not trusted by
tag name alone. See [the source lock](fonts.lock.json).

The Font Awesome Brands archive is a **standalone, opt-in app asset**. The
font's open license does not grant rights to third-party trademarks. Use
individual marks only to represent their owners; do not imply affiliation.
See [Font Awesome's license](https://github.com/FortAwesome/Font-Awesome/blob/14c65a3747d0f3b751f15831fc719236aea8729d/LICENSE.txt).
Neither the full Brands font nor Noto Emoji 2D is included in either
phone profile or mounted module. Local assembly derives only the single
compatibility mark described below; there is no system-wide logo library or
default Emoji replacement.
The 3D build and manufacturer-specific logo/fallback libraries are out of scope.

## Optional: assemble a private local test module

The test module contains a configuration **derived from the phone's own ROM**.
Keep it and all ROM inputs off GitHub and unshared. The local module builder
never runs in hosted CI. Backup and recovery access are prerequisites; disable
competing font modules and reboot first.

**Target: KernelSU Manager app only, with a working system mount backend.**
Current KernelSU needs a working metamodule such as meta-overlayfs to mount
`system/`. The installer checks the expected active metamodule marker; this
does not prove that the actual mount works. For a device already known to use a
legacy KernelSU built-in mount, explicitly select
`--mount-backend ksu-legacy-unverified`. That backend is unverified and does
not skip product, SDK, ROM identity or hash checks. Magisk, APatch, custom
recovery flashing, and physical-device acceptance were not tested.

### 1. Capture and preflight

Use these commands only if you already have authorized ADB access. These commands
read the phone; they do not install anything.

```sh
mkdir -p rom
adb pull /system/etc/font_fallback.xml rom/font_fallback.xml
adb shell getprop ro.build.fingerprint > rom/fingerprint.txt
adb shell getprop ro.product.device
adb shell getprop ro.build.version.sdk
adb shell sha256sum /system/etc/font_fallback.xml
```

Use the returned device, SDK and 64-digit digest below:

```sh
python3 build.py doctor --profile op6-slim \
  --inputs-zip dist/FontRoutes-Open-op6-slim-inputs-0.2.0.zip \
  --product DEVICE_FROM_GETPROP --sdk SDK_FROM_GETPROP \
  --rom-xml rom/font_fallback.xml --rom-xml-sha256 ROM_SHA256 \
  --fingerprint-file rom/fingerprint.txt
```

For OnePlus 13 use `op13-full` and its matching ZIP. The read-only doctor
validates the supplied snapshot, selected profile and optional public ZIP. Its
report does not include the fingerprint or device hashes and cannot claim
actual kernel mounting. It fails on a mismatched digest, unknown XML layout
or conflicting family, alias target or `fallbackFor` relationship.

On Android 15 / SDK 35, [AOSP chooses through a runtime flag](https://android.googlesource.com/platform/frameworks/base/+/refs/tags/android-15.0.0_r1/graphics/java/android/graphics/fonts/SystemFonts.java).
Verify which file your build **actually reads** and add
`--confirm-api35-active-config` to doctor and assembly. File presence is not
proof, even when both exist. Android 16/17 AOSP uses the modern path for normal
startup, not an automatic fallback to legacy if that file is absent.

If a custom ROM **actually reads legacy `/system/etc/fonts.xml`** and the
modern file is absent, capture and hash that legacy path instead and add
`--rom-config fonts.xml --confirm-legacy-rom-path` to both doctor and assembly.
Do not infer active routing merely from the presence of `fonts.xml`. The
installer rejects the legacy route if modern `font_fallback.xml` exists.
The builder will not overlay product/vendor font configs or blindly replace
both files.

### 2. Assemble, then verify separately

Only after preflight, prepare the optional Python environment for the default
logo fallback:

```sh
python3 -m venv .logo-venv
.logo-venv/bin/python -m pip install -r requirements-logo.txt
```

Then rerun the same preflight arguments with `.logo-venv/bin/python build.py
local-module` in place of `python3 build.py doctor`, and add
`--i-accept-local-rom-overlay`. The output is an explicit
`local-dist/*-LOCAL-DO-NOT-SHARE-*.zip`. Do not install it unless you accept
local test risk and can recover. The installer stages only and verifies the
selected ROM, manager, compatible product/SDK, backend marker, distinct
font paths and staged file hashes. No whole-directory replacement, no hot
install, boot scripts, system partition writes, SELinux changes or Google Play
changes. Disable the module **and reboot before any OTA or ROM switch**.

After a deliberate test install and reboot, compare read-only `adb shell
sha256sum` results for only the exact absolute paths listed in the ZIP's
`MOUNTED-SHA256SUMS.txt`. Matching the mounted bytes is not proof of rendering:
check app font selection, two reboots, and Google/Play cold starts separately.
If needed use KernelSU disable/uninstall or your preverified recovery route.

The XML edit appends only new **named** families, preserving OEM XML bytes,
existing file links/fallback order and variable axes. An app must select
`fontroutes-open-sans` or `fontroutes-open-cjk-sc`; Full also offers
`fontroutes-open-mono`, `fontroutes-open-math` and `fontroutes-open-wenkai`.
System defaults are unchanged, and OEM customization may still affect app
registration. Both local paths were tested on synthetic snapshots; none was
flashed.

References: [AOSP font fallback](https://source.android.com/docs/core/fonts/custom-font-fallback),
[KernelSU module guide](https://kernelsu.org/guide/module.html), and
[KernelSU mounting requirements](https://kernelsu.org/guide/metamodule.html).

### Local U+F8FF compatibility mark

Both profiles append one **unnamed** fallback family after the existing ROM
families by default. The font contains only U+F8FF in its two Unicode cmaps and
comes from the exact pinned Font Awesome Free Brands 7.3.1 U+F179 outline.
Neither U+F179 nor ASCII is routed through the generated font. Existing ROM
XML and family order are kept, so an earlier OEM glyph can win. Apps may also
choose their own fonts or a different fallback route. Confirm on-device with
a normal text app and representative ASCII, missing-glyph and U+F8FF samples;
a successful pack is not a rendering test.

The subset has a new FontRoutes name, bundled upstream OFL notice and separate
trademark and modification notice. No official Apple font is used, and no
trademark right or affiliation is implied. Only the local ROM-bound ZIP
contains this subset; no derivative font or ROM file is uploaded by CI.

To assemble without network, provide both the matching `--inputs-zip` and
`--logos-zip dist/FontRoutes-Open-logos-brand-use-0.2.0.zip`, and add
`--offline` in local-module mode. To leave U+F8FF behavior untouched (and avoid
the Python dependency), add `--no-logo-fallback` and omit `--logos-zip`.
