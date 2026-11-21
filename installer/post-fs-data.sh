#!/system/bin/sh
# Documented KernelSU pre-mount stage. No writes outside this module's metadata.
MODDIR="${0%/*}"
refuse() {
  : > "$MODDIR/skip_mount" || exit 1
  printf '%s\n' "FontRoutes paused: $1. Remove/disable, reboot and reinstall on the current ROM."     > "$MODDIR/ROM-GUARD-STATUS.txt"
  exit 0
}
[ ! -e "$MODDIR/skip_mount" ] || exit 0
{
  IFS= read -r config && IFS= read -r fp && IFS= read -r stock
} < "$MODDIR/ROM-GUARD.txt" || refuse "staging guard missing"
case "$config" in fonts.xml|font_fallback.xml) ;; *) refuse "unexpected config";; esac
if [ "$config" = fonts.xml ] && { [ -e /system/etc/font_fallback.xml ] || [ -L /system/etc/font_fallback.xml ]; }; then
  refuse "font selection changed"
fi
[ -n "$fp" ] && [ -n "$stock" ] || refuse "incomplete staging guard"
[ -f /data/adb/metamodule/metamount.sh ] && [ -r /data/adb/metamodule/metamount.sh ] &&
  [ ! -e /data/adb/metamodule/disable ] && [ ! -e /data/adb/metamodule/remove ] || refuse "mount provider unavailable"
for candidate in /data/adb/modules/*; do
  [ -f "$candidate/module.prop" ] || continue
  [ "${candidate##*/}" != "${MODDIR##*/}" ] || continue
  [ ! -e "$candidate/disable" ] && [ ! -e "$candidate/remove" ] && [ ! -e "$candidate/skip_mount" ] || continue
  for content in "$candidate" "/data/adb/metamodule/mnt/${candidate##*/}"; do
    [ ! -e "$content/system/etc/$config" ] && [ ! -L "$content/system/etc/$config" ] ||
      refuse "another enabled module also supplies the font XML"
  done
done
fingerprint="$(getprop ro.build.fingerprint)"
[ -n "$fingerprint" ] || refuse "current fingerprint unavailable"
actual="$(printf '%s' "$fingerprint" | sha256sum)" || refuse "cannot read build fingerprint"
[ "${actual%% *}" = "$fp" ] || refuse "build fingerprint changed"
[ -f "/system/etc/$config" ] && [ ! -L "/system/etc/$config" ] || refuse "stock XML unavailable"
actual="$(sha256sum "/system/etc/$config")" || refuse "cannot read stock XML"
[ "${actual%% *}" = "$stock" ] || refuse "stock XML changed"
exit 0
