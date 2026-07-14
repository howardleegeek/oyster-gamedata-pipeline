package world.oyster.recorder.depth;

/**
 * Pure-math helpers for converting Minecraft's GL depth buffer values into
 * view-space linear depth (metres along the optical Z axis), with no
 * dependency on Minecraft, Fabric, LWJGL, or OpenGL.
 *
 * <p>Howard 2026-05-13 (G198): this class exists so the projection-inversion
 * math is unit-testable on a Mac dev box without a JDK + LWJGL stack. The
 * same math runs on the GPU readback path in {@code RealDepthExporter} and
 * is mirrored bit-for-bit in {@code tests/test_real_depth_math.py} for the
 * Python validator. Any change here MUST also update the Python mirror —
 * the two are the canonical authority on the depth contract the buyer
 * accepts (PRD §3.4, BUYER_SPEC_V1 "Depth requirements").
 *
 * <h2>Coordinate conventions</h2>
 * <ul>
 *   <li>OpenGL classic: depth buffer ∈ [0, 1], NDC z = depth*2 - 1, near at z=0
 *   <li>Reversed-z (Minecraft 1.17+ optional): depth buffer ∈ [0, 1] but near=1
 *   <li>Returned linear depth is ALWAYS positive metres along +Z (towards scene)
 * </ul>
 *
 * <h2>Invalid-pixel contract (PRD §3.4)</h2>
 * <ul>
 *   <li>Sky / clipped-at-far / depth ≥ {@link #INVALID_DEPTH_THRESHOLD}: emit 0.0
 *   <li>Negative / NaN / Inf inputs: emit 0.0
 *   <li>Out-of-range (> far): emit 0.0
 *   <li>Near-clip artifacts (≤ 0): emit 0.0
 * </ul>
 */
public final class DepthMath {

    private DepthMath() {}

    /**
     * Threshold above which a normalised GL depth value is treated as
     * "sky / clipped far / invalid". The buyer-spec PRD §3.4 mandates 0.0
     * for these pixels so the depth gate (lint v3 #15/#16) doesn't reject
     * the clip on legitimate sky pixels.
     *
     * <p>Why 0.999 — Minecraft's far plane is typically 64 chunks × 16 m =
     * ~1024 m, with classic-z depth crammed into the last 0.1 % of the
     * buffer due to projection non-linearity. Anything in that bucket is
     * effectively unbounded.
     */
    public static final float INVALID_DEPTH_THRESHOLD = 0.999f;

    /**
     * Convert a single classic-z depth-buffer sample into linear metres.
     *
     * <p>Formula: {@code linear = (2 * near * far) / (far + near - z_ndc * (far - near))}
     * where {@code z_ndc = z_buf * 2 - 1}.
     *
     * @param depthBuf normalised depth-buffer value, expected in [0, 1].
     * @param near     near-plane distance in metres (must be > 0).
     * @param far      far-plane distance in metres (must be > near).
     * @return linear metres, or {@code 0.0f} if the input is invalid per
     *         PRD §3.4. Never returns NaN or Inf.
     */
    public static float linearDepthClassic(float depthBuf, float near, float far) {
        if (!isFinite(depthBuf) || !isFinite(near) || !isFinite(far)) return 0.0f;
        if (near <= 0.0f || far <= near) return 0.0f;
        if (depthBuf < 0.0f || depthBuf > 1.0f) return 0.0f;
        if (depthBuf >= INVALID_DEPTH_THRESHOLD) return 0.0f;
        float zNdc = depthBuf * 2.0f - 1.0f;
        float denom = (far + near) - zNdc * (far - near);
        if (denom == 0.0f) return 0.0f;
        float linear = (2.0f * near * far) / denom;
        if (!isFinite(linear) || linear <= 0.0f || linear > far) return 0.0f;
        return linear;
    }

    /**
     * Convert a single reversed-z depth sample (near at 1, far at 0) into
     * linear metres. Symmetric to {@link #linearDepthClassic}; we flip the
     * input via {@code 1 - depthBuf} before applying the same formula.
     *
     * @param depthBuf normalised reversed-z depth-buffer value, [0, 1].
     * @param near     near-plane distance in metres.
     * @param far      far-plane distance in metres.
     * @return linear metres or 0.0f per the invalid-pixel contract.
     */
    public static float linearDepthReversed(float depthBuf, float near, float far) {
        if (!isFinite(depthBuf)) return 0.0f;
        if (depthBuf < 0.0f || depthBuf > 1.0f) return 0.0f;
        // After flip, the "sky / clipped" pixels live near 1.0 again.
        float classic = 1.0f - depthBuf;
        return linearDepthClassic(classic, near, far);
    }

    /**
     * Vectorised conversion of an entire depth-buffer scanline. Operates
     * in-place into {@code outMetres} so callers can pool buffers and
     * avoid per-frame GC churn at 6 fps × 1920×1080 = ~12 MB/sec.
     *
     * @param depthBuf normalised depth-buffer samples (length = pixels).
     * @param outMetres pre-allocated output array, same length as input.
     * @param near near-plane in metres.
     * @param far  far-plane in metres.
     * @param reversedZ whether to apply the reversed-z flip first.
     * @throws IllegalArgumentException if input/output lengths differ.
     */
    public static void linearizeBuffer(
        float[] depthBuf,
        float[] outMetres,
        float near,
        float far,
        boolean reversedZ
    ) {
        if (depthBuf.length != outMetres.length) {
            throw new IllegalArgumentException(
                "depthBuf length " + depthBuf.length
                    + " != outMetres length " + outMetres.length);
        }
        for (int i = 0; i < depthBuf.length; i++) {
            outMetres[i] = reversedZ
                ? linearDepthReversed(depthBuf[i], near, far)
                : linearDepthClassic(depthBuf[i], near, far);
        }
    }

    /**
     * Count how many entries in {@code metres} are invalid (== 0.0f) per
     * the PRD §3.4 convention. Used by the in-mod self-check that warns
     * if a frame's invalid ratio exceeds {@link #MAX_INVALID_RATIO_HARD}.
     */
    public static int countInvalid(float[] metres) {
        int n = 0;
        for (float v : metres) {
            if (v == 0.0f) n++;
        }
        return n;
    }

    /**
     * Hard ceiling on invalid-pixel ratio: above this the frame is flagged
     * as "scene too sky-heavy / projection misconfigured" and the mod's
     * self-check logs a warning. The lint v3 buyer threshold is 5 %; we
     * leave headroom and warn at 4 % so operators see drift before it
     * fails buyer acceptance.
     */
    public static final float MAX_INVALID_RATIO_HARD = 0.05f;
    public static final float MAX_INVALID_RATIO_WARN = 0.04f;

    private static boolean isFinite(float v) {
        return !Float.isNaN(v) && !Float.isInfinite(v);
    }
}
