#!/usr/bin/env bash
#
# depth_anything_offline_install.sh
# HF mirror + offline cache pre-warm (China-friendly).
#
set -euo pipefail
trap 'rm -rf "$TMP_DIR"' EXIT

DEFAULT_SIZE="base"
HF_MIRROR="${HF_MIRROR:-https://hf-mirror.com}"
CACHE_DIR="${HF_HOME:-${XDG_CACHE_HOME:-$HOME/.cache}/huggingface}"

usage() {
    cat <<EOF
Usage: $(basename "$0") [--model-size small|base|large]

Pre-warm Depth Anything model cache (China-friendly).
EOF
    exit 0
}

MODEL_SIZE="${DEFAULT_SIZE}"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --model-size) MODEL_SIZE="$2"; shift 2 ;;
        -h|--help) usage ;;
        *) echo "Error: unknown $1" >&2; exit 1 ;;
    esac
done

[[ "${MODEL_SIZE}" =~ ^(small|base|large)$ ]] || { echo "Invalid size"; exit 1; }

declare -A MODEL_IDS
MODEL_IDS[small]="LiheYoung/Depth-Anything-Small"
MODEL_IDS[base]="LiheYoung/Depth-Anything-Base"
MODEL_IDS[large]="LiheYoung/Depth-Anything-Large"

MODEL_ID="${MODEL_IDS[${MODEL_SIZE}]}"
TARGET_DIR="${CACHE_DIR}/hub/models--${MODEL_ID//\//--}"

echo "==> Depth Anything offline install (size: ${MODEL_SIZE})"
mkdir -p "${CACHE_DIR}/hub"

[[ -d "${TARGET_DIR}" ]] && { echo "Already cached: ${TARGET_DIR}"; exit 0; }

TMP_DIR=$(mktemp -d)
cd "${TMP_DIR}"

echo "==> Downloading from ${HF_MIRROR}/${MODEL_ID}..."
if command -v huggingface-cli &>/dev/null; then
    HF_MIRROR="${HF_MIRROR}" huggingface-cli download "${MODEL_ID}" \
        --local-dir "${TARGET_DIR}" --local-dir-use-symlinks False
else
    mkdir -p "${TARGET_DIR}"
    wget -q -N "${HF_MIRROR}/${MODEL_ID}/resolve/main/config.json" -P "${TARGET_DIR}" || true
    wget -q -N "${HF_MIRROR}/${MODEL_ID}/resolve/main/pytorch_model.bin" -P "${TARGET_DIR}" || true
fi

echo "==> Cached at ${TARGET_DIR}"
