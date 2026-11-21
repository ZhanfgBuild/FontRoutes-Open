# FontRoutes Open

Independent, open-input font builder for **OnePlus 13 Full** and **OnePlus 6
Slim** profiles. It does not import source, fonts, releases, ROM files, or Git
history from any private project. The project code is MIT; bundled unmodified
font bytes retain their SIL Open Font License 1.1 and upstream notices. This is
not a vendor product or a drop-in replacement for a full private root module.

| Profile | Exact public inputs | Local assembly target |
| --- | --- | --- |
| `op13-full` | Noto Sans Regular/Bold, Noto Sans Mono Regular, Noto Sans CJK SC Regular, STIX Two Math Regular | Listed OnePlus 13 product identifiers, SDK 35–37, and the exact local ROM XML/fingerprint used to assemble |
| `op6-slim` | Noto Sans Regular and Noto Sans CJK SC Regular | Listed OnePlus 6 product identifiers on SDK 37 and the exact local ROM XML/fingerprint used to assemble |

Product/SDK allowlists are **validation gates**, not a compatibility claim.
Neither profile reproduces private fonts, Apple/vendor glyph extensions,
third-party root binaries, custom Emoji, automatic system routing, or the full
historical weight/fallback suite. OEM default fonts and Emoji are not changed.
Slim uses two font faces only; the broadly covered CJK face still dominates
size.

## Build public inputs

Python 3.11+; building from upstream needs HTTPS.

```sh
python3 -m unittest discover -s tests -v
python3 build.py inputs --profile op13-full
python3 build.py inputs --profile op6-slim
```

These produce two public inputs ZIPs and SHA-256 sidecars in `dist/`. **Do not
flash the inputs ZIPs.** They contain font assets, the exact OFL notices, and a
source manifest. Every source and license is pinned to an upstream commit and
a SHA-256 in `fonts.lock.json`. The Actions matrix tests and builds both profiles
independently, uploads only `dist/` inputs, and has no release/publish step.

## Optional: assemble a private local test module

This step is deliberately unavailable in hosted CI. A local test module
contains a configuration **derived from the phone's own ROM**, never part of
a public input ZIP. Keep that module and all ROM inputs off GitHub and unshared.

The intended test route is **KernelSU Manager app** with an already-working
systemless mount setup. Do not install a manager or mount provider merely to
follow this guide. This builder was tested with synthetic ROM XML and mocked
installer checks only, **not** on a physical device, specific ColorOS/custom
ROM, Magisk, another root manager, or recovery flashing. Before selecting the
result in KernelSU, confirm backup and recovery access, disable competing font
modules, and reboot. Disable this local module before any OTA or ROM switch. Do
not proceed if the stock ROM has no readable `/system/etc/font_fallback.xml`.

Capture from the specific device with ADB **only if you already have
authorized ADB access**. These commands read, not modify, the phone.

```sh
mkdir -p rom
adb pull /system/etc/font_fallback.xml rom/font_fallback.xml
adb shell getprop ro.build.fingerprint > rom/fingerprint.txt
adb shell getprop ro.product.device
adb shell getprop ro.build.version.sdk
adb shell sha256sum /system/etc/font_fallback.xml
```

Use the reported device value, SDK, and the 64-digit digest. Example placeholders
are not defaults:

```sh
python3 build.py local-module \
  --profile op6-slim \
  --inputs-zip dist/FontRoutes-Open-op6-slim-inputs-0.1.0.zip \
  --product DEVICE_FROM_GETPROP --sdk SDK_FROM_GETPROP \
  --rom-xml rom/font_fallback.xml --rom-xml-sha256 ROM_SHA256 \
  --fingerprint-file rom/fingerprint.txt --i-accept-local-rom-overlay
```

For OnePlus 13, select `op13-full` and the matching input ZIP. Alternatively omit
`--inputs-zip` to download assets again from the same public lock. Local output
goes under `local-dist/` and is prominently marked `LOCAL-DO-NOT-SHARE`.

The builder inserts new **named** families at the end of the supplied
`familyset`. It preserves the original XML bytes, existing file links, named
families, alias references, fallback order and variable axes; conflicts with
an existing route name, alias target or `fallbackFor` are rejected. AOSP named
families are opt-in, and OEM customization can still affect registration.
This **does not change system defaults** or force unrelated apps to select
these fonts. A test app can explicitly select `fontroutes-open-sans`,
`fontroutes-open-cjk-sc`, or on Full `fontroutes-open-mono` and
`fontroutes-open-math`. Named-family or cross-process availability still
needs device verification.

The installer checks the exact current device, SDK, fingerprint and baseline
XML SHA-256 before declaring success. It does not write the system partition,
delete caches, install a boot hook, alter SELinux, or touch Google Play
Services. This gate runs at **install** time, not after future OTAs. Device
acceptance still requires boot/reboot coverage, app selection, Google/Play
cold starts, and rollback checks. Use the root manager's disable/uninstall
path or your preverified recovery route if anything misbehaves.

Further reading: [AOSP font fallback](https://source.android.com/docs/core/fonts/custom-font-fallback)
and [KernelSU mounting requirements](https://kernelsu.org/guide/metamodule.html).
