package world.oyster.recorder;

/**
 * Immutable per-tick game-state sample.
 *
 * <p>Howard 2026-05-07: chose record over class for serialisation simplicity
 * and to make the field set the canonical contract between mod and recorder.
 * If you change a field name here, update both
 * {@code recorder_consumer_lite.py:_consume_game_state_jsonl} and the
 * {@code action_camera.json} schema in {@code buyer_spec_adapter.py}.
 *
 * <p>All angles in degrees; all distances in blocks (= metres in MC's
 * {@code metric_scale=1.0} convention); timestamps in ms-since-epoch.
 */
public record GameStateSample(
    long tick,
    long timestampMs,
    double x, double y, double z,
    float yawDeg, float pitchDeg,
    double lookX, double lookY, double lookZ,
    double velocityX, double velocityY, double velocityZ,
    boolean onGround,
    boolean sneaking,
    boolean sprinting,
    String dimension,
    String gameMode
) {
    /**
     * Serialise to a single-line JSON string. No external JSON dep — Fabric
     * mods should be lean, and this object's shape is fixed at compile time.
     * Any future schema change must update this method (and the consumer).
     */
    public String toJsonLine() {
        StringBuilder sb = new StringBuilder(256);
        sb.append('{');
        appendKv(sb, "tick", tick);                 sb.append(',');
        appendKv(sb, "timestamp_ms", timestampMs);  sb.append(',');
        appendKv(sb, "x", x);                       sb.append(',');
        appendKv(sb, "y", y);                       sb.append(',');
        appendKv(sb, "z", z);                       sb.append(',');
        appendKv(sb, "yaw_deg", yawDeg);            sb.append(',');
        appendKv(sb, "pitch_deg", pitchDeg);        sb.append(',');
        appendKv(sb, "look_x", lookX);              sb.append(',');
        appendKv(sb, "look_y", lookY);              sb.append(',');
        appendKv(sb, "look_z", lookZ);              sb.append(',');
        appendKv(sb, "velocity_x", velocityX);      sb.append(',');
        appendKv(sb, "velocity_y", velocityY);      sb.append(',');
        appendKv(sb, "velocity_z", velocityZ);      sb.append(',');
        appendKv(sb, "on_ground", onGround);        sb.append(',');
        appendKv(sb, "sneaking", sneaking);         sb.append(',');
        appendKv(sb, "sprinting", sprinting);       sb.append(',');
        appendKvStr(sb, "dimension", dimension);    sb.append(',');
        appendKvStr(sb, "game_mode", gameMode);
        sb.append('}');
        return sb.toString();
    }

    // --- minimal JSON primitives (no Gson dep) ---

    private static void appendKv(StringBuilder sb, String k, long v) {
        sb.append('"').append(k).append('"').append(':').append(v);
    }
    private static void appendKv(StringBuilder sb, String k, double v) {
        sb.append('"').append(k).append('"').append(':').append(v);
    }
    private static void appendKv(StringBuilder sb, String k, float v) {
        sb.append('"').append(k).append('"').append(':').append(v);
    }
    private static void appendKv(StringBuilder sb, String k, boolean v) {
        sb.append('"').append(k).append('"').append(':').append(v ? "true" : "false");
    }
    private static void appendKvStr(StringBuilder sb, String k, String v) {
        sb.append('"').append(k).append('"').append(':');
        sb.append('"');
        // minimal escape — MC dimension/gamemode strings never contain " or \
        for (int i = 0; i < v.length(); i++) {
            char c = v.charAt(i);
            if (c == '"' || c == '\\') sb.append('\\');
            sb.append(c);
        }
        sb.append('"');
    }
}
