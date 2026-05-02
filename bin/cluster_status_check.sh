#!/bin/bash
#
# cluster_status_check.sh - Quick health probe of cluster nodes
# Checks disk space, ffmpeg, python3, and Aliyun key file on each node
#

set -euo pipefail

# Node definitions
NODES=("mac-2" "minipc-bwdxs")

# Aliyun key file path to check
ALIYUN_KEY_PATH="$HOME/.aliyun/key.pem"

# Disk usage threshold (percentage)
DISK_THRESHOLD=90

# Results storage
declare -A RESULTS

# Function to check a single node
check_node() {
    local node="$1" pass=0 fail=0
    echo "Checking node: $node"

    # SSH connectivity
    if ssh -o ConnectTimeout=5 -o BatchMode=yes "$node" "echo ok" &>/dev/null; then
        echo "  SSH: PASS"; ((pass++))
    else
        echo "  SSH: FAIL"; RESULTS["$node"]="FAIL"; return
    fi

    # Disk space
    local disk=$(ssh "$node" "df / | tail -1 | awk '{print \$5}' | tr -d '%'" 2>/dev/null || echo 100)
    if [[ $disk -lt $DISK_THRESHOLD ]]; then echo "  Disk: PASS (${disk}%)"; ((pass++)); else echo "  Disk: FAIL (${disk}%)"; ((fail++)); fi

    # ffmpeg
    if ssh "$node" "command -v ffmpeg &>/dev/null" 2>/dev/null; then echo "  ffmpeg: PASS"; ((pass++)); else echo "  ffmpeg: FAIL"; ((fail++)); fi

    # python3
    if ssh "$node" "command -v python3 &>/dev/null" 2>/dev/null; then echo "  python3: PASS"; ((pass++)); else echo "  python3: FAIL"; ((fail++)); fi

    # Aliyun key
    if ssh "$node" "test -f $ALIYUN_KEY_PATH" 2>/dev/null; then echo "  Aliyun key: PASS"; ((pass++)); else echo "  Aliyun key: FAIL"; ((fail++)); fi

    RESULTS["$node"]=$([[ $fail -eq 0 ]] && echo "PASS" || echo "FAIL")
}

echo "========================================"
echo "  Cluster Status Check - $(date '+%Y-%m-%d %H:%M:%S')"
echo "========================================"

for node in "${NODES[@]}"; do
    check_node "$node"
    echo "----------------------------------------"
done

echo -e "\n=== SUMMARY TABLE ==="
printf "%-20s | %-10s\n" "NODE" "STATUS"
echo "----------------------|------------"
for node in "${NODES[@]}"; do
    status="${RESULTS[$node]:-UNKNOWN}"
    [[ "$status" == "PASS" ]] && printf "%-20s | \033[32m%s\033[0m\n" "$node" "$status" || printf "%-20s | \033[31m%s\033[0m\n" "$node" "$status"
done

for node in "${NODES[@]}"; do [[ "${RESULTS[$node]:-FAIL}" != "PASS" ]] && exit 1; done
exit 0