#!/system/bin/sh
# Sourced by KernelSU. Only stages module contents under MODPATH.
[ "$BOOTMODE" = true ] && [ "$KSU" = true ] && [ "$ARCH" = arm64 ] ||
  abort "Install this ZIP in KernelSU Manager on ARM64, not in recovery or another manager."
case "$MODPATH" in /*) ;; *) abort "KernelSU did not supply an absolute staging path.";; esac
[ -d "$MODPATH" ] && [ ! -L "$MODPATH" ] || abort "Unexpected staging directory."
[ ! -e "$MODPATH/skip_mount" ] || abort "Remove the old FontRoutes module, reboot, then reinstall."
[ -f /data/adb/metamodule/metamount.sh ] && [ -r /data/adb/metamodule/metamount.sh ] &&
    [ ! -e /data/adb/metamodule/disable ] && [ ! -e /data/adb/metamodule/remove ] ||
  abort "Needs an enabled KernelSU system overlay metamodule. Install/activate it and reboot first."
command -v sha256sum >/dev/null 2>&1 || abort "sha256sum unavailable."
valid_hash() {
  [ "${#1}" -eq 64 ] || return 1
  case "$1" in *[!0-9a-f]*) return 1;; esac
}
product="$(getprop ro.product.device)"
sdk="$(getprop ro.build.version.sdk)"
case "@PROFILE@:$product" in
  op13-full:PJZ110|op13-full:CPH2649|op13-full:CPH2653|op13-full:CPH2655|op13-full:OP5D0DL1|op13-full:dodge|op13-full:OnePlus13) ;;
  op6-slim:A6000|op6-slim:A6003|op6-slim:enchilada|op6-slim:OnePlus6) ;;
  *) abort "Wrong device for the @PROFILE@ package." ;;
esac
case "@PROFILE@:$sdk" in
  op13-full:36|op13-full:37|op6-slim:36|op6-slim:37) config=font_fallback.xml ;;
  op6-slim:28|op6-slim:29|op6-slim:30|op6-slim:31|op6-slim:32|op6-slim:33|op6-slim:34)
    [ ! -e /system/etc/font_fallback.xml ] && [ ! -L /system/etc/font_fallback.xml ] ||
      abort "This older ROM exposes a modern font config; the active file is ambiguous."
    config=fonts.xml ;;
  *) abort "Unsupported or ambiguous Android font config (SDK $sdk). No system files were changed." ;;
esac
rom="/system/etc/$config"
[ -f "$rom" ] && [ -r "$rom" ] && [ ! -L "$rom" ] ||
  abort "Expected stock font XML is missing, unreadable, or a symlink."
for candidate in /data/adb/modules/* /data/adb/modules_update/*; do
  [ -f "$candidate/module.prop" ] || continue
  [ "${candidate##*/}" != "fontroutes-open-@PROFILE@" ] || continue
  [ ! -e "$candidate/disable" ] && [ ! -e "$candidate/remove" ] && [ ! -e "$candidate/skip_mount" ] || continue
  for content in "$candidate" "/data/adb/metamodule/mnt/${candidate##*/}"; do
    [ ! -e "$content/system/etc/$config" ] && [ ! -L "$content/system/etc/$config" ] ||
      abort "Another enabled module stages the same font XML. Disable it, reboot and retry."
  done
done
fingerprint="$(getprop ro.build.fingerprint)"
[ -n "$fingerprint" ] || abort "ROM fingerprint unavailable."
sha="$(printf '%s' "$fingerprint" | sha256sum)" || abort "Cannot hash fingerprint."
fp="${sha%% *}"
valid_hash "$fp" || abort "Invalid fingerprint hash."
for entry in "$MODPATH"/system/fonts/*; do
  [ -f "$entry" ] && [ ! -L "$entry" ] || abort "Missing or invalid staged font payload."
  name="${entry##*/}"
  [ ! -e "/system/fonts/$name" ] && [ ! -L "/system/fonts/$name" ] ||
    abort "A FontRoutes font is already mounted. Remove/disable the old font module and reboot first."
done
(cd "$MODPATH" && sha256sum -c PAYLOAD-SHA256SUMS.txt >/dev/null 2>&1) ||
  abort "ZIP payload integrity check failed."
[ ! -e "$MODPATH/system/etc" ] && [ ! -L "$MODPATH/system/etc" ] ||
  abort "Unexpected prebuilt font XML in the ZIP."
mkdir "$MODPATH/system/etc" || abort "Cannot create staged config directory."
set_perm "$MODPATH/system/etc" 0 0 0755 || abort "Cannot set staged config directory permissions/context."
set_perm "$MODPATH/bin/fr-xml" 0 0 0755 || abort "Cannot enable ARM64 stager permissions/context."
set_perm "$MODPATH/post-fs-data.sh" 0 0 0755 || abort "Cannot enable boot guard permissions/context."
hashes="$("$MODPATH/bin/fr-xml" "$rom" "$MODPATH/FAMILIES.xml" "$MODPATH/system/etc/$config")" ||
  abort "ROM XML refused. Disable overlapping font modules and retry; no live system files were changed."
stock="${hashes%% *}"
patched="${hashes#* }"
valid_hash "$stock" && valid_hash "$patched" || abort "XML stager did not return two SHA-256 values."
actual="$(sha256sum "$rom")" || abort "Cannot recheck stock XML."
[ "${actual%% *}" = "$stock" ] || abort "Stock XML changed while the package was being staged."
set_perm "$MODPATH/system/etc/$config" 0 0 0644 || abort "Cannot label the generated font XML."
actual="$(sha256sum "$MODPATH/system/etc/$config")" || abort "Cannot recheck staged XML."
[ "${actual%% *}" = "$patched" ] || abort "Staged XML failed integrity verification."
printf '%s\n%s\n%s\n%s\n' "$config" "$fp" "$stock" "$patched" > "$MODPATH/ROM-GUARD.txt" ||
  abort "Cannot save ROM guard state."
ui_print "FontRoutes staged @PROFILE@. Reboot; this is not a hot install."
ui_print "Keep other font overlay modules disabled. Disable FontRoutes before OTA or ROM changes."
