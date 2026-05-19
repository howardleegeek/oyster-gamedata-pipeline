#!/usr/bin/env bash
#
# depth_anything_offline_install.sh
# HF mirror + offline cache pre-warm (China-friendly).
#
set -euo pipefail
trap '[[ -n "${TMP_DIR:-}" ]] && rm -rf "${TMP_DIR}"' EXIT

HF_MIRROR="${HF_MIRROR:-https://hf-mirror.com}"
CACHE_DIR="${HF_HOME:-${XDG_CACHE_HOME:-$HOME/.cache}/huggingface}/hub"

MODEL_SMALL="LiheYoung/Depth-Anything-Small"
MODEL_BASE="LiheYoung/Depth-Anything-Base"
MODEL_LARGE="LiheYoung/Depth-Anything-Large"

usage() {
    echo "Usage: $(basename "$0") [--model-size small|base|large]"
    echo "Pre-warm Depth Anything model cache (China-friendly)."
    exit 0
}

MODEL_SIZE="base"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --model-size) MODEL_SIZE="$2"; shift 2 ;;
        -h|--help) usage ;;
        *) echo "Error: unknown $1" >&2; exit 1 ;;
    esac
done

case "${MODEL_SIZE}" in
    small) MODEL_ID="${MODEL_SMALL}" ;;
    base)  MODEL_ID="${MODEL_BASE}" ;;
    large) MODEL_ID="${MODEL_LARGE}" ;;
    *) echo "Invalid size: ${MODEL_SIZE}" >&2; exit 1 ;;
esac

TARGET_DIR="${CACHE_DIR}/models--${MODEL_ID//\//--}"
FILES=("config.json" "pytorch_model.bin" "preprocessor_config.json")

echo "==> Depth Anything offline install (size: ${MODEL_SIZE})"
mkdir -p "${CACHE_DIR}"

# Check if already cached
CACHED=true
for file in "${FILES[@]}"; do
    if [[ ! -f "${TARGET_DIR}/${file}" ]]; then
        CACHED=false
        break
    fi
done

if [[ "${CACHED}" == true ]]; then
    echo "Already cached: ${TARGET_DIR}"
    exit 0
fi

TMP_DIR=$(mktemp -d)
cd "${TMP_DIR}"

echo "==> Downloading from ${HF_MIRROR}/${MODEL_ID}..."
SUCCESS=true
for file in "${FILES[@]}"; do
    URL="${HF_MIRROR}/${MODEL_ID}/resolve/main/${file}"
    echo "    ${file}"
    if ! wget -q -N "${URL}" -P "${TARGET_DIR}" 2>/dev/null; then
        echo "    Warning: failed to download ${file}" >&2
        SUCCESS=false
    fi
done

if [[ "${SUCCESS}" == false ]]; then
    echo "Error: Failed to download model" >&2
    exit 1
fi

echo "==> Successfully cached at ${TARGET_DIR}"