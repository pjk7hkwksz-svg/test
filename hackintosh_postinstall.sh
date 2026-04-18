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
# Deletes disk0s3 (confirmed disposable) from the GPT and creates a 500 MB
# EFI System Partition in the freed space.  The remainder (~462 GB) is left
# as unallocated — the APFS container already has 507 GB free.
create_efi_partition() {
    log "=== Step 1: Create EFI partition on $SSD_DISK ==="

    # If an EFI partition is already present, skip creation entirely
    EFI_PART=$(diskutil list "$SSD_DISK" 2>/dev/null \
        | awk '/^[[:space:]]+[0-9]+:[[:space:]]+EFI[[:space:]]/ {print $NF; exit}')
    if [[ -n "$EFI_PART" ]]; then
        log "EFI partition already present: $EFI_PART — skipping creation"
        return 0
    fi

    # Confirm the disposable partition still exists
    diskutil info "${SSD_DISK}s${SSD_DISPOSABLE_PART_IDX}" &>/dev/null \
        || die "${SSD_DISK}s${SSD_DISPOSABLE_PART_IDX} not found — cannot proceed"

    # Read the GPT sector layout
    local gpt_out
    gpt_out=$(gpt show "$SSD_DISK" 2>/dev/null) \
        || die "'gpt show $SSD_DISK' failed — is $SSD_DISK a GPT disk?"
    log "GPT layout before changes:"
    printf '%s\n' "$gpt_out" >> "$LOG"

    # Extract start and size (in 512-byte sectors) for the disposable partition
    local part_start part_size
    part_start=$(awk -v i="${SSD_DISPOSABLE_PART_IDX}" '$3 == i {print $1; exit}' \
        <<< "$gpt_out")
    part_size=$(awk  -v i="${SSD_DISPOSABLE_PART_IDX}" '$3 == i {print $2; exit}' \
        <<< "$gpt_out")

    [[ -n "$part_start" && -n "$part_size" ]] \
        || die "Could not parse partition ${SSD_DISPOSABLE_PART_IDX} sectors from 'gpt show'"

    local efi_sectors=$(( EFI_SIZE_MB * 1024 * 1024 / 512 ))
    log "${SSD_DISK}s${SSD_DISPOSABLE_PART_IDX}: start=${part_start} size=${part_size} sectors"
    log "EFI will use ${EFI_SIZE_MB} MB = ${efi_sectors} sectors starting at sector ${part_start}"

    # Unmount the disposable partition if mounted (best-effort)
    diskutil unmount "${SSD_DISK}s${SSD_DISPOSABLE_PART_IDX}" 2>/dev/null || true

    # Remove the GPT partition entry
    log "Removing GPT entry for ${SSD_DISK}s${SSD_DISPOSABLE_PART_IDX}..."
    gpt remove -i "$SSD_DISPOSABLE_PART_IDX" "$SSD_DISK" 2>&1 | tee -a "$LOG" \
        || die "gpt remove failed. If 'Resource busy', run manually: sudo gpt remove -i ${SSD_DISPOSABLE_PART_IDX} /dev/rdisk0"

    # Add 500 MB EFI System Partition at the same starting sector
    log "Adding ${EFI_SIZE_MB} MB EFI System Partition..."
    gpt add -b "$part_start" -s "$efi_sectors" -t "$EFI_GUID" "$SSD_DISK" 2>&1 | tee -a "$LOG" \
        || die "gpt add failed"

    # Give diskutil time to notice the new partition
    sleep 2
    diskutil repairDisk "$SSD_DISK" &>/dev/null || true
    sleep 1

    # Locate the new partition (poll up to 10 s)
    local retries=5
    for (( i=0; i<retries; i++ )); do
        EFI_PART=$(diskutil list "$SSD_DISK" 2>/dev/null \
            | awk '/^[[:space:]]+[0-9]+:[[:space:]]+EFI[[:space:]]/ {print $NF; exit}')
        [[ -n "$EFI_PART" ]] && break
        sleep 2
    done
    [[ -n "$EFI_PART" ]] \
        || die "New EFI partition not visible after gpt add — run: diskutil list $SSD_DISK"

    # Format as FAT32 with label "EFI"
    log "Formatting /dev/r${EFI_PART} as FAT32..."
    newfs_msdos -F 32 -v EFI "/dev/r${EFI_PART}" 2>&1 | tee -a "$LOG" \
        || die "newfs_msdos failed"

    log "EFI partition created: $EFI_PART"
}

# ── Step 2: Mount the SSD EFI partition ───────────────────────────────────────
mount_ssd_efi() {
    log "=== Step 2: Mount EFI partition ==="

    # Resolve EFI_PART on a re-run where Step 1 was skipped
    if [[ -z "$EFI_PART" ]]; then
        EFI_PART=$(diskutil list "$SSD_DISK" 2>/dev/null \
            | awk '/^[[:space:]]+[0-9]+:[[:space:]]+EFI[[:space:]]/ {print $NF; exit}')
        [[ -n "$EFI_PART" ]] || die "No EFI partition found on $SSD_DISK"
    fi

    mkdir -p "$EFI_MOUNT"

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
