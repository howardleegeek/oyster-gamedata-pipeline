#!/usr/bin/env bash
#
# depth_anything_offline_install.sh
# HF mirror + offline cache pre-warm (China-friendly).
#
set -euo pipefail
trap '[[ -n "${TMP_DIR:-}" ]] && rm -rf "${TMP_DIR}"' EXIT

DEFAULT_SIZE="base"
HF_MIRROR="${HF_MIRROR:-https://hf-mirror.com}"
CACHE_DIR="${HF_HOME:-${XDG_CACHE_HOME:-$HOME/.cache}/huggingface}/hub"

# Model IDs
MODEL_SMALL="LiheYoung/Depth-Anything-Small"
MODEL_BASE="LiheYoung/Depth-Anything-Base"
MODEL_LARGE="LiheYoung/Depth-Anything-Large"

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

case "${MODEL_SIZE}" in
    small) MODEL_ID="${MODEL_SMALL}" ;;
    base)  MODEL_ID="${MODEL_BASE}" ;;
    large) MODEL_ID="${MODEL_LARGE}" ;;
    *) echo "Invalid size: ${MODEL_SIZE}" >&2; exit 1 ;;
esac

TARGET_DIR="${CACHE_DIR}/models--${MODEL_ID//\//--}"

echo "==> Depth Anything offline install (size: ${MODEL_SIZE})"
mkdir -p "${CACHE_DIR}"

if [[ -f "${TARGET_DIR}/pytorch_model.bin" ]] && [[ -f "${TARGET_DIR}/config.json" ]]; then
    echo "Already cached: ${TARGET_DIR}"
    exit 0
fi

TMP_DIR=$(mktemp -d)
cd "${TMP_DIR}"

echo "==> Downloading from ${HF_MIRROR}/${MODEL_ID}..."
if command -v huggingface-cli &>/dev/null; then
    HF_MIRROR="${HF_MIRROR}" huggingface-cli download "${MODEL_ID}" \
        --local-dir "${TARGET_DIR}" --local-dir-use-symlinks False
else
    mkdir -p "${TARGET_DIR}"
    for file in config.json pytorch_model.bin preprocessor_config.json; do
        wget -q -N "${HF_MIRROR}/${MODEL_ID}/resolve/main/${file}" -P "${TARGET_DIR}" || true
    done
fi

if [[ -f "${TARGET_DIR}/pytorch_model.bin" ]]; then
    echo "==> Successfully cached at ${TARGET_DIR}"
else
    echo "Error: Failed to download model" >&2
    exit 1
fi