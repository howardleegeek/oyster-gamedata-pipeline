#!/bin/bash
set -e
# =============================================================================
# mod_build_orchestrator.sh
# 
# Apply depth_zbuffer_capture.diff to mc-mod-fabric and build the mod.
# =============================================================================

# 1. Locate mc-mod-fabric/ (default path from spec)
MOD_DIR=${1:-/Users/howardli/Downloads/gamedata-recorder/mc-mod-fabric}

# Resolve to absolute path
MOD_DIR=$(cd "$MOD_DIR" 2>/dev/null && pwd)

if [ ! -d "$MOD_DIR" ]; then
    echo "ERROR: mc-mod-fabric directory not found: $MOD_DIR"
    echo "Please provide a valid path to mc-mod-fabric/"
    exit 1
fi

echo "=== Mod Build Orchestrator ==="
echo "MOD_DIR: $MOD_DIR"

# 2. Find the patch
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PATCH_DIR="$SCRIPT_DIR/patches"
PATCH_FILE="$PATCH_DIR/depth_zbuffer_capture.diff"

if [ ! -f "$PATCH_FILE" ]; then
    # Try alternative locations
    PATCH_FILE="/private/tmp/cluster-2026-05-18-integration/integrated/patches/depth_zbuffer_capture.diff"
fi

if [ ! -f "$PATCH_FILE" ]; then
    echo "ERROR: depth_zbuffer_capture.diff not found"
    exit 1
fi

echo "PATCH_FILE: $PATCH_FILE"

# 3. Check if patch applies cleanly (dry run)
echo ""
echo "=== Step 1: Dry-run patch application ==="
if git -C "$MOD_DIR" apply --check "$PATCH_FILE" 2>&1; then
    echo "Patch applies cleanly (dry-run)"
else
    echo "WARNING: Patch may not apply cleanly - checking for conflicts..."
    # Try with fuzz
    if git -C "$MOD_DIR" apply --check --3way "$PATCH_FILE" 2>&1; then
        echo "Patch applies with 3-way merge"
    else
        echo "ERROR: Patch cannot be applied cleanly"
        exit 1
    fi
fi

# 4. Apply the patch
echo ""
echo "=== Step 2: Apply patch ==="
git -C "$MOD_DIR" apply "$PATCH_FILE" 2>&1 || {
    echo "ERROR: Failed to apply patch"
    exit 1
}

# 5. Verify the new file exists
DEPTH_FILE="$MOD_DIR/src/main/java/com/example/depthmod/DepthCaptureMod.java"
if [ ! -f "$DEPTH_FILE" ]; then
    echo "ERROR: DepthCaptureMod.java not created after patch"
    exit 1
fi
echo "Created: $DEPTH_FILE"

# 6. Build the mod
echo ""
echo "=== Step 3: Build with Gradle ==="
cd "$MOD_DIR"

# Check for gradlew
if [ -f "./gradlew" ]; then
    chmod +x ./gradlew
    ./gradlew build --no-daemon 2>&1
else
    echo "ERROR: gradlew not found in $MOD_DIR"
    exit 1
fi

# 7. Find resulting jar
echo ""
echo "=== Step 4: Find built JAR ==="
JAR=$(find "$MOD_DIR/build/libs" -name '*.jar' -not -name '*-sources.jar' -not -name '*-dev.jar' | head -1)

if [ -z "$JAR" ]; then
    echo "ERROR: No JAR file found in build/libs/"
    exit 1
fi

JAR_SIZE=$(ls -lh "$JAR" | awk '{print $5}')
echo "Built: $JAR"
echo "Size: $JAR_SIZE"

# 8. Smoke test: verify mod structure
echo ""
echo "=== Step 5: Verify JAR contents ==="
if unzip -l "$JAR" | grep -q "DepthCaptureMod.class"; then
    echo "✓ DepthCaptureMod.class found in JAR"
else
    echo "WARNING: DepthCaptureMod.class not found in JAR"
fi

if unzip -l "$JAR" | grep -q "fabric.mod.json"; then
    echo "✓ fabric.mod.json found in JAR"
else
    echo "WARNING: fabric.mod.json not found in JAR"
fi

echo ""
echo "=== Build Complete ==="
echo "JAR: $JAR"
