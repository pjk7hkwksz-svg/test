#!/usr/bin/env bash
#
# Install AirportItlwm WiFi kext for Intel Wireless-AC 8265 on macOS Monterey
# Targets the OpenCore EFI on disk0 (Dell Latitude E7490 Hackintosh)
#
# Usage: sudo bash install_wifi_kext.sh

set -euo pipefail

readonly SSD_DISK="disk0"
readonly EFI_MOUNT="/Volumes/EFI_SSD"
readonly ITLWM_VER="2.3.0"
readonly ITLWM_URL="https://github.com/OpenIntelWireless/itlwm/releases/download/v${ITLWM_VER}/AirportItlwm_v${ITLWM_VER}_stable_Monterey.kext.zip"
readonly LOG="/var/log/hackintosh-wifi.log"

log() { printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$LOG"; }
die() { log "FATAL: $*"; exit 1; }

# ── Preconditions ─────────────────────────────────────────────────────────────
(( EUID == 0 )) || die "Run as root: sudo bash $0"
sw_vers -productVersion | grep -q '^12\.' \
    || log "WARNING: Expected macOS 12.x (Monterey); kext may not match"

# ── Find and mount EFI partition ──────────────────────────────────────────────
EFI_PART=$(diskutil list "$SSD_DISK" 2>/dev/null \
    | awk '/^[[:space:]]+[0-9]+:[[:space:]]+(EFI|Windows_FAT_32|MS-DOS)[[:space:]]/ {print $NF; exit}')
[[ -n "$EFI_PART" ]] || die "No EFI/FAT32 partition found on $SSD_DISK — run the full post-install script first"

mkdir -p "$EFI_MOUNT"
if ! mount | grep -qF " $EFI_MOUNT "; then
    # Remount from wherever diskutil may have auto-mounted it
    diskutil unmount "$EFI_PART" 2>/dev/null || true
    diskutil mount -mountPoint "$EFI_MOUNT" "$EFI_PART" 2>&1 | tee -a "$LOG" \
        || die "Could not mount $EFI_PART"
fi

KEXTS_DIR="${EFI_MOUNT}/EFI/OC/Kexts"
CONFIG="${EFI_MOUNT}/EFI/OC/config.plist"

[[ -d "$KEXTS_DIR" ]] || die "Kexts dir not found at $KEXTS_DIR — EFI not transferred yet"
[[ -f "$CONFIG"    ]] || die "config.plist not found at $CONFIG"

# ── Install kext bundle ───────────────────────────────────────────────────────
KEXT_DST="${KEXTS_DIR}/AirportItlwm.kext"

if [[ -d "$KEXT_DST" ]]; then
    log "AirportItlwm.kext already present — skipping copy"
else
    src=""
    for candidate in \
        "/Volumes/MAC/AirportItlwm.kext" \
        "$HOME/Downloads/AirportItlwm.kext" \
        "$HOME/Desktop/AirportItlwm.kext" \
        "/tmp/AirportItlwm.kext"; do
        [[ -d "$candidate" ]] && { src="$candidate"; break; }
    done

    if [[ -n "$src" ]]; then
        log "Copying pre-downloaded kext from $src"
        cp -R "$src" "$KEXT_DST"
    else
        log "Downloading AirportItlwm v${ITLWM_VER} for Monterey..."
        tmp_zip="/tmp/AirportItlwm_Monterey.zip"
        tmp_dir="/tmp/itlwm_extract"
        curl -fL --progress-bar "$ITLWM_URL" -o "$tmp_zip" 2>&1 | tee -a "$LOG" \
            || die "Download failed. Connect ethernet or place AirportItlwm.kext at /Volumes/MAC/ and re-run."
        rm -rf "$tmp_dir"
        unzip -q "$tmp_zip" -d "$tmp_dir"
        extracted=$(find "$tmp_dir" -maxdepth 3 -name "AirportItlwm.kext" | head -1)
        [[ -n "$extracted" ]] || die "AirportItlwm.kext not found in archive"
        cp -R "$extracted" "$KEXT_DST"
        rm -rf "$tmp_dir" "$tmp_zip"
    fi
    log "Kext installed at $KEXT_DST"
fi

# ── Patch config.plist ────────────────────────────────────────────────────────
log "Patching config.plist..."
python3 - "$CONFIG" <<'PYEOF' 2>&1 | tee -a "$LOG" || die "config.plist patch failed"
import sys, plistlib

path = sys.argv[1]
with open(path, 'rb') as f:
    pl = plistlib.load(f)

add_list = pl.setdefault('Kernel', {}).setdefault('Add', [])

if any('AirportItlwm' in e.get('BundlePath', '') for e in add_list):
    print("AirportItlwm already in Kernel:Add — no change needed")
    sys.exit(0)

add_list.append({
    'Arch':           'x86_64',
    'BundlePath':     'Kexts/AirportItlwm.kext',
    'Comment':        'Intel Wireless-AC 8265',
    'Enabled':        True,
    'ExecutablePath': 'Contents/MacOS/AirportItlwm',
    'MaxKernel':      '',
    'MinKernel':      '',
    'PlistPath':      'Contents/Info.plist',
})

with open(path, 'wb') as f:
    plistlib.dump(pl, f, fmt=plistlib.FMT_XML, sort_keys=False)

print("AirportItlwm entry added to Kernel:Add")
PYEOF

log "Done. Reboot (without USB) for WiFi to appear."
