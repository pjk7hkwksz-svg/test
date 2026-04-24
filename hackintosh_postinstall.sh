#!/usr/bin/env bash
#
# Hackintosh post-install script
# Dell Latitude E7490 | Intel i5-8350U | macOS Monterey 12.x
# OpenCore 1.0.5 | Intel Wireless-AC 8265 (device-id 0x24FD)
#
# Usage: sudo bash hackintosh_postinstall.sh
# Idempotent: re-running skips completed steps.
# This script does NOT reboot automatically.

set -euo pipefail

# ── Configuration ─────────────────────────────────────────────────────────────
readonly USB_EFI_SRC="/Volumes/MAC/EFI"
readonly SSD_DISK="disk0"
readonly SSD_DISPOSABLE_PART_IDX=3    # disk0s3 — confirmed disposable by user
readonly EFI_SIZE_MB=500
readonly EFI_GUID="C12A7328-F81F-11D2-BA4B-00A0C93EC93B"
readonly ITLWM_VER="2.3.0"
readonly ITLWM_URL="https://github.com/OpenIntelWireless/itlwm/releases/download/v${ITLWM_VER}/AirportItlwm_v${ITLWM_VER}_stable_Monterey.kext.zip"
readonly LOG="/var/log/hackintosh-postinstall.log"
readonly STATE_DIR="/.hackintosh_postinstall"
readonly EFI_MOUNT="/Volumes/EFI_SSD"

# Set by create_efi_partition, used by later steps
EFI_PART=""

# ── Logging ───────────────────────────────────────────────────────────────────
log()  { printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$LOG"; }
die()  { log "FATAL: $*"; exit 1; }
warn() { log "WARNING: $*"; }

# ── Idempotency helpers ───────────────────────────────────────────────────────
is_done()  { [[ -f "${STATE_DIR}/$1.done" ]]; }
mark_done() {
    mkdir -p "$STATE_DIR"
    touch "${STATE_DIR}/$1.done"
    log "  -> Step '$1' marked complete"
}

# ── Step 0: Preconditions ─────────────────────────────────────────────────────
preconditions() {
    log "=== Preconditions ==="
    (( EUID == 0 )) || die "Run as root: sudo bash $0"
    [[ -d "$USB_EFI_SRC" ]] \
        || die "USB EFI not found at $USB_EFI_SRC — insert the Hackintosh USB and re-run"
    [[ -f "$USB_EFI_SRC/OC/config.plist" ]] \
        || die "config.plist missing at $USB_EFI_SRC/OC/config.plist"
    diskutil info "$SSD_DISK" &>/dev/null \
        || die "Disk $SSD_DISK not found"
    local ver
    ver=$(sw_vers -productVersion)
    [[ "$ver" == 12.* ]] \
        || warn "Expected macOS 12.x (Monterey), detected $ver — AirportItlwm kext may not match"
    log "Preconditions OK"
}

# ── Step 1: Create EFI partition on disk0 ────────────────────────────────────
#
# Strategy: diskutil eraseVolume reformats disk0s3 as FAT32 through disk
# arbitration (no exclusive device lock required, works on a live boot disk).
# We then attempt gpt -f to fix the partition type GUID to the EFI System
# Partition value.  If that also fails, Dell UEFI firmware will still boot
# from a plain FAT32 partition that contains /EFI/BOOT/BOOTx64.efi.
# disk0s3 is confirmed disposable; its full ~463 GB becomes the EFI partition.
create_efi_partition() {
    log "=== Step 1: Create EFI partition on $SSD_DISK ==="

    # If an EFI-type partition is already present, skip creation entirely
    EFI_PART=$(diskutil list "$SSD_DISK" 2>/dev/null \
        | awk '/^[[:space:]]+[0-9]+:[[:space:]]+EFI[[:space:]]/ {print $NF; exit}')
    if [[ -n "$EFI_PART" ]]; then
        log "EFI partition already present: $EFI_PART — skipping creation"
        return 0
    fi

    # A previous partial run may have left disk0s3 as FAT32 but with the wrong
    # GUID.  Accept it as-is rather than reformatting again.
    local msdos_part
    msdos_part=$(diskutil list "$SSD_DISK" 2>/dev/null \
        | awk '/^[[:space:]]+[0-9]+:[[:space:]]+(Windows_FAT_32|MS-DOS)[[:space:]]/ {print $NF; exit}')
    if [[ -n "$msdos_part" ]]; then
        log "Found FAT32 partition $msdos_part from a previous run — reusing"
        EFI_PART="$msdos_part"
        return 0
    fi

    # Confirm the disposable partition still exists
    diskutil info "${SSD_DISK}s${SSD_DISPOSABLE_PART_IDX}" &>/dev/null \
        || die "${SSD_DISK}s${SSD_DISPOSABLE_PART_IDX} not found — cannot proceed"

    # Capture sector layout now (needed for the GUID fixup attempt below)
    local gpt_out part_start part_size
    gpt_out=$(gpt show "$SSD_DISK" 2>/dev/null) || true
    log "GPT layout before changes:"
    printf '%s\n' "$gpt_out" >> "$LOG"
    part_start=$(awk -v i="${SSD_DISPOSABLE_PART_IDX}" '$3 == i {print $1; exit}' <<< "$gpt_out")
    part_size=$(awk  -v i="${SSD_DISPOSABLE_PART_IDX}" '$3 == i {print $2; exit}' <<< "$gpt_out")

    # ── Primary: reformat disk0s3 as FAT32 via diskutil ──────────────────────
    # diskutil goes through Disk Arbitration and does not need an exclusive
    # device lock, so it works even though disk0s2 is the live boot volume.
    # The partition keeps Microsoft Basic Data GUID for now; we fix it below.
    log "Reformatting ${SSD_DISK}s${SSD_DISPOSABLE_PART_IDX} as FAT32 (MS-DOS) via diskutil..."
    diskutil eraseVolume MS-DOS EFI "${SSD_DISK}s${SSD_DISPOSABLE_PART_IDX}" 2>&1 | tee -a "$LOG" \
        || die "diskutil eraseVolume failed"

    # ── Secondary: fix partition type GUID to EFI System Partition ───────────
    # gpt -f bypasses the exclusive-lock check.  This may still fail on some
    # Monterey configurations; if so we warn and continue with the FAT32 GUID.
    if [[ -n "$part_start" && -n "$part_size" ]]; then
        log "Attempting EFI GUID fixup via gpt -f..."
        if gpt -f remove -i "$SSD_DISPOSABLE_PART_IDX" "/dev/r${SSD_DISK}" 2>&1 | tee -a "$LOG" \
        && gpt -f add -b "$part_start" -s "$part_size" -t "$EFI_GUID" "/dev/r${SSD_DISK}" 2>&1 | tee -a "$LOG"; then
            log "EFI System Partition GUID set successfully"
        else
            warn "GUID fixup failed — partition remains FAT32 (Microsoft Basic Data type)"
            warn "Dell Latitude E7490 UEFI will still boot from a FAT32 partition containing /EFI/BOOT/BOOTx64.efi"
        fi
    fi

    sleep 2
    diskutil repairDisk "$SSD_DISK" &>/dev/null || true
    sleep 1

    # Locate the resulting partition (EFI type or FAT32 fallback)
    local retries=5
    for (( i=0; i<retries; i++ )); do
        EFI_PART=$(diskutil list "$SSD_DISK" 2>/dev/null \
            | awk '/^[[:space:]]+[0-9]+:[[:space:]]+(EFI|Windows_FAT_32|MS-DOS)[[:space:]]/ {print $NF; exit}')
        [[ -n "$EFI_PART" ]] && break
        sleep 2
    done
    [[ -n "$EFI_PART" ]] \
        || die "Partition not visible after format — run: diskutil list $SSD_DISK"

    log "EFI partition created: $EFI_PART"
}

# ── Step 2: Mount the SSD EFI partition ───────────────────────────────────────
mount_ssd_efi() {
    log "=== Step 2: Mount EFI partition ==="

    # Resolve EFI_PART on a re-run where Step 1 was skipped.
    # Accept EFI type or FAT32 (MS-DOS / Windows_FAT_32) as a valid EFI partition.
    if [[ -z "$EFI_PART" ]]; then
        EFI_PART=$(diskutil list "$SSD_DISK" 2>/dev/null \
            | awk '/^[[:space:]]+[0-9]+:[[:space:]]+(EFI|Windows_FAT_32|MS-DOS)[[:space:]]/ {print $NF; exit}')
        [[ -n "$EFI_PART" ]] || die "No EFI/FAT32 partition found on $SSD_DISK"
    fi

    mkdir -p "$EFI_MOUNT"

    # diskutil eraseVolume auto-mounts the freshly formatted partition.
    # If it mounted somewhere other than EFI_MOUNT, remount it where we expect.
    local current_mount
    current_mount=$(mount | awk -v d="/dev/${EFI_PART}" '$1==d {print $3}')
    if [[ -n "$current_mount" && "$current_mount" != "$EFI_MOUNT" ]]; then
        log "Partition auto-mounted at $current_mount — remounting at $EFI_MOUNT"
        diskutil unmount "$EFI_PART" 2>&1 | tee -a "$LOG" || true
    fi

    if mount | grep -qF " $EFI_MOUNT "; then
        log "Already mounted at $EFI_MOUNT"
        return 0
    fi

    log "Mounting $EFI_PART at $EFI_MOUNT..."
    diskutil mount -mountPoint "$EFI_MOUNT" "$EFI_PART" 2>&1 | tee -a "$LOG" \
        || die "diskutil mount failed for $EFI_PART"
    log "Mounted"
}

# ── Step 3: Transfer EFI from USB → SSD ──────────────────────────────────────
transfer_efi() {
    log "=== Step 3: Transfer EFI (USB → SSD) ==="
    is_done "efi_transferred" && { log "Already done — skipping"; return 0; }

    mkdir -p "${EFI_MOUNT}/EFI"

    log "rsync ${USB_EFI_SRC}/ → ${EFI_MOUNT}/EFI/ ..."
    rsync -a --delete "$USB_EFI_SRC/" "${EFI_MOUNT}/EFI/" 2>&1 | tee -a "$LOG" \
        || die "rsync failed"

    # Spot-check with md5
    local src_md5 dst_md5
    src_md5=$(md5 -q "$USB_EFI_SRC/OC/config.plist")
    dst_md5=$(md5 -q "${EFI_MOUNT}/EFI/OC/config.plist")
    [[ "$src_md5" == "$dst_md5" ]] \
        || die "config.plist checksum mismatch (src=$src_md5, dst=$dst_md5)"
    log "Transfer verified (md5=$src_md5)"

    mark_done "efi_transferred"
}

# ── Step 4: Install AirportItlwm WiFi kext ────────────────────────────────────
install_wifi_kext() {
    log "=== Step 4: Install AirportItlwm (Intel 8265, 0x24FD, Monterey) ==="
    is_done "wifi_kext_installed" && { log "Already done — skipping"; return 0; }

    local kext_dst="${EFI_MOUNT}/EFI/OC/Kexts/AirportItlwm.kext"
    local config_plist="${EFI_MOUNT}/EFI/OC/config.plist"

    # ── Copy kext bundle ──────────────────────────────────────────────────────
    if [[ -d "$kext_dst" ]]; then
        log "AirportItlwm.kext already present — skipping copy"
    else
        local src_kext=""
        for candidate in \
            "/Volumes/MAC/AirportItlwm.kext" \
            "$HOME/Downloads/AirportItlwm.kext" \
            "$HOME/Desktop/AirportItlwm.kext" \
            "/tmp/AirportItlwm.kext"; do
            [[ -d "$candidate" ]] && { src_kext="$candidate"; break; }
        done

        if [[ -n "$src_kext" ]]; then
            log "Using pre-downloaded kext: $src_kext"
            cp -R "$src_kext" "$kext_dst"
        else
            log "Downloading AirportItlwm v${ITLWM_VER} for Monterey..."
            local tmp_zip="/tmp/AirportItlwm_Monterey.zip"
            local tmp_dir="/tmp/itlwm_extract"
            curl -fL --progress-bar "$ITLWM_URL" -o "$tmp_zip" 2>&1 | tee -a "$LOG" \
                || die "Download failed. No ethernet? Place AirportItlwm.kext (Monterey build) at /Volumes/MAC/ and re-run."
            rm -rf "$tmp_dir"
            unzip -q "$tmp_zip" -d "$tmp_dir"
            local extracted
            extracted=$(find "$tmp_dir" -maxdepth 3 -name "AirportItlwm.kext" | head -1)
            [[ -n "$extracted" ]] || die "AirportItlwm.kext not found in downloaded archive"
            cp -R "$extracted" "$kext_dst"
            rm -rf "$tmp_dir" "$tmp_zip"
        fi
        log "AirportItlwm.kext placed at $kext_dst"
    fi

    # ── Patch config.plist via Python (idempotent) ────────────────────────────
    log "Patching config.plist..."
    python3 - "$config_plist" <<'PYEOF' 2>&1 | tee -a "$LOG" || die "config.plist patch failed"
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

    mark_done "wifi_kext_installed"
}

# ── Step 5: Bless SSD EFI (writes NVRAM boot entry) ─────────────────────────
bless_efi() {
    log "=== Step 5: Bless SSD EFI ==="
    is_done "efi_blessed" && { log "Already done — skipping"; return 0; }

    bless \
        --mount "$EFI_MOUNT" \
        --setBoot \
        --file  "${EFI_MOUNT}/EFI/OC/OpenCore.efi" \
        --shortform \
        2>&1 | tee -a "$LOG" \
        && mark_done "efi_blessed" \
        || warn "bless failed — set boot order manually in BIOS: F2 → Boot Sequence → EFI on ${SSD_DISK}"
}

# ── Summary ───────────────────────────────────────────────────────────────────
print_summary() {
    log ""
    log "=================================================="
    log "  Post-install complete — DO NOT reboot yet"
    log "=================================================="
    log "  EFI partition : ${EFI_PART}  =>  ${EFI_MOUNT}"
    log "  OpenCore      : ${EFI_MOUNT}/EFI/OC/"
    log "  WiFi kext     : AirportItlwm v${ITLWM_VER} (Intel 8265)"
    log "  Full log      : ${LOG}"
    log ""
    log "  MANUAL STEPS REQUIRED:"
    log "  1. Shut down and remove the USB drive"
    log "  2. Power on -> press F2 -> Boot Sequence"
    log "  3. Move EFI on ${SSD_DISK} to top of boot order"
    log "  4. Save -> reboot — WiFi should appear in menu bar"
    log "=================================================="
}

# ── Main ──────────────────────────────────────────────────────────────────────
mkdir -p "$(dirname "$LOG")"
log "== Hackintosh post-install started =="
log "System: $(sw_vers -productName) $(sw_vers -productVersion) | $(uname -m) | PID $$"

preconditions
create_efi_partition
mount_ssd_efi
transfer_efi
install_wifi_kext
bless_efi
print_summary
