#!/usr/bin/env bash
# ------------------------------------------------------------
# force_vfio_binds.sh - bind a whole IOMMU group to vfio-pci,
# run PCILeechFWGenerator, then restore everything.
# ------------------------------------------------------------
set -euo pipefail

usage() {
  cat <<EOF
Usage: $0 -d <BDF> -b <donor-board> [-p <generator-path>]

  -d  PCI device BDF to clone (e.g. 0000:05:00.0)
  -b  Donor board name passed to pcileech.py (e.g. pcileech_75t484_x1)
  -p  Path to PCILeechFWGenerator root (default: \$HOME/PCILeechFWGenerator)
EOF
  exit 1
}

# ---------- parse CLI --------------------------------------------------------
GEN_PATH="$HOME/PCILeechFWGenerator"
BDF=""
BOARD=""

while getopts ":d:b:p:h" opt; do
  case "$opt" in
    d) BDF="$OPTARG" ;;
    b) BOARD="$OPTARG" ;;
    p) GEN_PATH="$OPTARG" ;;
    h|*) usage ;;
  esac
done

[[ -z $BDF || -z $BOARD ]] && usage

# ---------- discover IOMMU group & devices -----------------------------------
GROUP_DIR=$(readlink -f /sys/bus/pci/devices/$BDF/iommu_group) \
  || { echo "❌  Device $BDF has no IOMMU group - is IOMMU on?"; exit 1; }

GROUP=$(basename "$GROUP_DIR")
mapfile -t GROUP_DEVS < <(basename -a "$GROUP_DIR"/devices/*)

echo "ℹ️  BDF $BDF is in IOMMU group $GROUP with: ${GROUP_DEVS[*]}"

# ---------- record current drivers -------------------------------------------
declare -A ORIGINAL_DRIVER
for dev in "${GROUP_DEVS[@]}"; do
  drv_link="/sys/bus/pci/devices/$dev/driver"
  if [[ -L $drv_link ]]; then
    ORIGINAL_DRIVER[$dev]=$(basename "$(readlink -f "$drv_link")")
  else
    ORIGINAL_DRIVER[$dev]=none
  fi
done

# ---------- helper: cleanup ---------------------------------------------------
cleanup() {
  echo "↩️  Restoring original drivers..."
  for dev in "${GROUP_DEVS[@]}"; do
    [[ ${ORIGINAL_DRIVER[$dev]} == "vfio-pci" ]] && continue

    # unbind from vfio-pci if it is currently bound
    if [[ -L /sys/bus/pci/devices/$dev/driver ]] \
       && [[ $(basename "$(readlink -f /sys/bus/pci/devices/$dev/driver)") == "vfio-pci" ]]; then
      echo "$dev" | sudo tee /sys/bus/pci/devices/$dev/driver/unbind >/dev/null
    fi

    # re-bind to original driver if one existed
    if [[ ${ORIGINAL_DRIVER[$dev]} != none ]]; then
      echo "$dev" | sudo tee /sys/bus/pci/drivers/${ORIGINAL_DRIVER[$dev]}/bind >/dev/null
    fi

    # remove temporary ID from vfio-pci
    if [[ ${ORIGINAL_DRIVER[$dev]} != "vfio-pci" ]]; then
      VID=$(cat /sys/bus/pci/devices/$dev/vendor)
      DID=$(cat /sys/bus/pci/devices/$dev/device)
      echo "${VID/0x/} ${DID/0x/}" | sudo tee /sys/bus/pci/drivers/vfio-pci/remove_id >/dev/null
    fi
  done
}
trap cleanup EXIT

# ---------- bind whole group to vfio-pci -------------------------------------
echo "🔒  Binding group $GROUP to vfio-pci ..."
sudo modprobe vfio-pci

for dev in "${GROUP_DEVS[@]}"; do
  [[ ${ORIGINAL_DRIVER[$dev]} == "vfio-pci" ]] && continue

  # add ID to vfio-pci
  VID=$(cat /sys/bus/pci/devices/$dev/vendor)
  DID=$(cat /sys/bus/pci/devices/$dev/device)
  echo "${VID/0x/} ${DID/0x/}" | sudo tee /sys/bus/pci/drivers/vfio-pci/new_id >/dev/null

  # unbind from old driver (if any) and bind to vfio-pci
  [[ ${ORIGINAL_DRIVER[$dev]} != none ]] \
    && echo "$dev" | sudo tee /sys/bus/pci/devices/$dev/driver/unbind >/dev/null
  echo "$dev" | sudo tee /sys/bus/pci/drivers/vfio-pci/bind >/dev/null
done

# ---------- run the build -----------------------------------------------------
echo "🚀  Launching PCILeechFWGenerator ..."
cd "$GEN_PATH"
sudo -E python3 pcileech.py build --bdf "$BDF" --board "$BOARD"

echo "✅  Build finished - firmware should be in $GEN_PATH/output"
