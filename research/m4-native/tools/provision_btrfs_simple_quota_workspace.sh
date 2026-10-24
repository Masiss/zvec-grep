#!/usr/bin/env bash
# Provision the approved, persistent Btrfs simple-quota workspace for M4.
# Default is read-only. Apply only with: sudo bash <script> --apply
set -Eeuo pipefail

readonly MOUNTPOINT="/"
readonly WORKSPACE_SUBVOL="/home/masis/src/zvec-grep/research/m4-native/.work/m4-native-workspace-quota"
readonly ATTESTATION_PATH="/var/tmp/m4-native-workspace-quota-pwd.attestation.json"
readonly CAP="128M"
readonly CAP_BYTES="134217728"

usage() {
  printf 'Usage: %s [--apply]\n' "$0"
  printf 'Target: %s\n' "$WORKSPACE_SUBVOL"
  printf 'Default is read-only preflight. --apply creates this persistent subvolume\n'
  printf 'and applies a 128 MiB referenced-byte cap; it never runs a benchmark.\n'
}

APPLY=0
while (($#)); do
  case "$1" in
    --apply) APPLY=1 ;;
    --pwd-workspace) ;; # Compatibility with the one-time provisioning command.
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
  shift
done

command -v btrfs >/dev/null || { echo 'ERROR: btrfs-progs is not installed' >&2; exit 1; }
command -v findmnt >/dev/null || { echo 'ERROR: findmnt is not installed' >&2; exit 1; }

root_fstype=$(findmnt -n -o FSTYPE -T "$MOUNTPOINT")
workspace_parent=$(dirname "$WORKSPACE_SUBVOL")
workspace_fstype=$(findmnt -n -o FSTYPE -T "$workspace_parent")
root_target=$(findmnt -n -o TARGET -T "$MOUNTPOINT")
workspace_target=$(findmnt -n -o TARGET -T "$workspace_parent")
if [[ "$root_fstype" != btrfs || "$workspace_fstype" != btrfs || "$root_target" != "$workspace_target" ]]; then
  echo "ERROR: expected / and $workspace_parent on the same Btrfs mount (got $root_fstype@$root_target and $workspace_fstype@$workspace_target)" >&2
  exit 1
fi
if [[ $(realpath -e "$workspace_parent") != "/home/masis/src/zvec-grep/research/m4-native/.work" ]]; then
  echo "ERROR: workspace parent resolves outside the approved ignored .work directory" >&2
  exit 1
fi

printf 'Target filesystem: %s (mount %s)\n' "$root_fstype" "$root_target"
printf 'Quota workspace:  %s\n' "$WORKSPACE_SUBVOL"
printf 'Hard qgroup cap:  %s (%s bytes, referenced/compressed accounting)\n' "$CAP" "$CAP_BYTES"
printf 'Mode:             %s\n' "$([[ $APPLY -eq 1 ]] && echo APPLY || echo READ-ONLY PREFLIGHT)"

quota_status=$(btrfs quota status "$MOUNTPOINT")
printf '\nCurrent quota status:\n%s\n' "$quota_status"

if [[ $APPLY -eq 0 ]]; then
  printf '\nNo changes made. To apply, run: sudo bash %q --apply\n' "$0"
  exit 0
fi

if [[ $EUID -ne 0 ]]; then
  echo 'ERROR: --apply must run as root (for example, sudo bash script --apply)' >&2
  exit 1
fi
owner=${SUDO_USER:-}
if [[ -z "$owner" || "$owner" == root ]] || ! id "$owner" >/dev/null 2>&1; then
  echo 'ERROR: run through sudo from the intended non-root account; SUDO_USER is missing/invalid' >&2
  exit 1
fi

if grep -Eiq '^[[:space:]]*Enabled:[[:space:]]+no' <<<"$quota_status"; then
  printf '\nWARNING: enabling Btrfs quotas changes accounting filesystem-wide and may trigger metadata/rescan work.\n'
  printf 'It can affect write latency for unrelated workloads. No benchmark will be launched by this script.\n'
  read -r -p 'Type ENABLE-SIMPLE-QUOTAS-ON-ROOT to continue: ' confirmation
  if [[ "$confirmation" != ENABLE-SIMPLE-QUOTAS-ON-ROOT ]]; then
    echo 'Cancelled; no changes made.'
    exit 1
  fi
  btrfs quota enable --simple "$MOUNTPOINT"
  quota_status=$(btrfs quota status "$MOUNTPOINT")
else
  printf '\nThis will create only the selected persistent subvolume and set its 128 MiB cap.\n'
  printf 'It will not alter existing qgroups or run a benchmark.\n'
  read -r -p 'Type CREATE-M4-QUOTA-WORKSPACE to continue: ' confirmation
  if [[ "$confirmation" != CREATE-M4-QUOTA-WORKSPACE ]]; then
    echo 'Cancelled; no changes made.'
    exit 1
  fi
fi
if ! grep -Eiq '^[[:space:]]*Enabled:[[:space:]]+yes' <<<"$quota_status"; then
  printf 'ERROR: quota status does not confirm Enabled: yes; leaving state unchanged.\n%s\n' "$quota_status" >&2
  exit 1
fi
if ! grep -Eiq '^[[:space:]]*Mode:[[:space:]]+(squota|simple)([[:space:]]|$)' <<<"$quota_status"; then
  printf 'ERROR: filesystem is not confirmed in simple-quota mode; refusing to proceed.\n%s\n' "$quota_status" >&2
  exit 1
fi
if grep -Eiq '^[[:space:]]*Inconsistent:[[:space:]]+yes' <<<"$quota_status"; then
  printf 'ERROR: quota accounting is inconsistent; stop and inspect/rescan with an administrator.\n%s\n' "$quota_status" >&2
  exit 1
fi

if [[ -L "$WORKSPACE_SUBVOL" ]]; then
  echo "ERROR: refusing symlink at $WORKSPACE_SUBVOL" >&2
  exit 1
fi
if [[ -e "$WORKSPACE_SUBVOL" ]]; then
  if ! subvolume_info=$(btrfs subvolume show "$WORKSPACE_SUBVOL" 2>&1); then
    printf 'ERROR: path exists but is not a readable Btrfs subvolume: %s\n%s\n' "$WORKSPACE_SUBVOL" "$subvolume_info" >&2
    exit 1
  fi
  if find "$WORKSPACE_SUBVOL" -mindepth 1 -maxdepth 1 -print -quit | grep -q .; then
    echo "ERROR: existing subvolume is not empty; refusing to alter its cap/contents" >&2
    exit 1
  fi
else
  btrfs subvolume create "$WORKSPACE_SUBVOL"
  subvolume_info=$(btrfs subvolume show "$WORKSPACE_SUBVOL")
fi

chown "$owner:$(id -gn "$owner")" "$WORKSPACE_SUBVOL"
chmod 0700 "$WORKSPACE_SUBVOL"
btrfs qgroup limit "$CAP" "$WORKSPACE_SUBVOL"

# Verify the limit from the kernel-visible qgroup table; do not accept a silent setup failure.
qgroup_report=$(btrfs qgroup show --raw -r "$WORKSPACE_SUBVOL")
subvolume_id=$(awk -F': *' '/^[[:space:]]*Subvolume ID:/ { gsub(/^[[:space:]]+|[[:space:]]+$/, "", $2); print $2; exit }' <<<"$subvolume_info")
if [[ ! "$subvolume_id" =~ ^[0-9]+$ ]]; then
  printf 'ERROR: could not parse subvolume ID from details. Preserve this state for admin review.\n%s\n' "$subvolume_info" >&2
  exit 1
fi
if ! awk -v qgroup="0/$subvolume_id" -v cap="$CAP_BYTES" '$1 == qgroup && $4 == cap { found=1 } END { exit !found }' <<<"$qgroup_report"; then
  printf 'ERROR: qgroup output did not verify %s max referenced=%s bytes. Preserve this state for admin review.\n%s\n' "0/$subvolume_id" "$CAP_BYTES" "$qgroup_report" >&2
  exit 1
fi
quota_status=$(btrfs quota status "$MOUNTPOINT")
if grep -Eiq '^[[:space:]]*Inconsistent:[[:space:]]+yes' <<<"$quota_status"; then
  printf 'ERROR: quota accounting became inconsistent after setup; do not use this workspace.\n%s\n' "$quota_status" >&2
  exit 1
fi

filesystem_uuid=$(findmnt -n -o UUID -T "$WORKSPACE_SUBVOL")
actual_subvolume_id=$(btrfs inspect-internal rootid "$WORKSPACE_SUBVOL")
if [[ "$actual_subvolume_id" != "$subvolume_id" || -z "$filesystem_uuid" ]]; then
  printf 'ERROR: filesystem/subvolume identity does not match quota report (uuid=%s rootid=%s show_id=%s).\n' "$filesystem_uuid" "$actual_subvolume_id" "$subvolume_id" >&2
  exit 1
fi
if [[ -L "$ATTESTATION_PATH" || ( -e "$ATTESTATION_PATH" && ! -f "$ATTESTATION_PATH" ) ]]; then
  printf 'ERROR: refusing non-regular attestation path %s\n' "$ATTESTATION_PATH" >&2
  exit 1
fi
attestation_tmp=$(mktemp "$(dirname "$ATTESTATION_PATH")/.m4-native-workspace-quota-attestation.XXXXXX")
trap 'rm -f -- "${attestation_tmp:-}"' EXIT
verified_at=$(date -u +'%Y-%m-%dT%H:%M:%SZ')
printf '{"schema_version":1,"filesystem_uuid":"%s","mountpoint":"/","workspace_subvolume":"%s","subvolume_id":%s,"qgroup_id":"0/%s","max_referenced_bytes":%s,"accounting_mode":"squota","verified_at_utc":"%s"}\n' \
  "$filesystem_uuid" "$WORKSPACE_SUBVOL" "$subvolume_id" "$subvolume_id" "$CAP_BYTES" "$verified_at" >"$attestation_tmp"
chown root:root "$attestation_tmp"
chmod 0644 "$attestation_tmp"
mv -f -- "$attestation_tmp" "$ATTESTATION_PATH"
attestation_tmp=''

printf '\nSETUP_VERIFIED\n%s\n' "$quota_status"
printf '\nSubvolume details:\n%s\n' "$subvolume_info"
printf '\nVerified qgroup limit:\n%s\n' "$qgroup_report"
printf '\nRoot-owned capacity attestation:\n%s\n' "$ATTESTATION_PATH"
printf '\nWorkspace owner/mode: %s %s\n' "$(stat -c '%U:%G' "$WORKSPACE_SUBVOL")" "$(stat -c '%a' "$WORKSPACE_SUBVOL")"
printf 'No benchmark was run. Send this complete output back before using the path.\n'
