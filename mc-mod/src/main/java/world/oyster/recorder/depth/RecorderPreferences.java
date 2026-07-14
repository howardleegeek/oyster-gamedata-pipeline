package world.oyster.recorder.depth;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;

/**
 * Lightweight reader for {@code ~/Documents/OysterClips/preferences.json}.
 *
 * <p>Howard 2026-05-13 (G198): the mod must not depend on Gson, Jackson, or
 * Minecraft's own json libs from the recorder thread (we tick before the
 * world loads). For ONE flag, a regex scan is fine — fail safe to "off"
 * so the DA-V2 fallback path is the default and an absent or malformed
 * preferences.json never silently enables a half-tested shader path.
 *
 * <h2>File format</h2>
 * <pre>{@code
 * {
 *   "enable_real_depth_shader": true,
 *   "depth_near_metres": 0.05,
 *   "depth_far_metres": 1024.0,
 *   "depth_reversed_z": false,
 *   "depth_width": 1920,
 *   "depth_height": 1080,
 *   "depth_fps": 6
 * }
 * }</pre>
 *
 * Missing keys keep their defaults. Unknown keys are ignored.
 */
public final class RecorderPreferences {

    /** Default: disabled. Iron law: real-depth shader is opt-in. */
    public final boolean enableRealDepthShader;
    public final float nearMetres;
    public final float farMetres;
    public final boolean reversedZ;
    public final int width;
    public final int height;
    public final int fps;

    private RecorderPreferences(boolean enable, float near, float far, boolean reversedZ,
                                int width, int height, int fps) {
        this.enableRealDepthShader = enable;
        this.nearMetres = near;
        this.farMetres = far;
        this.reversedZ = reversedZ;
        this.width = width;
        this.height = height;
        this.fps = fps;
    }

    /** Defaults used when no preferences.json is present. */
    public static RecorderPreferences defaults() {
        return new RecorderPreferences(
            false,    // off by default — DA-V2 fallback is canonical
            0.05f,    // 5 cm near plane (matches MC's gl_Near with FOV-safe margin)
            1024.0f,  // 64 chunks × 16 m — MC's default render distance ceiling
            false,    // classic z by default; Minecraft only uses reversed-z if a mod enables it
            1920, 1080,
            6
        );
    }

    /**
     * Read the preferences file from its canonical location
     * ({@code ~/Documents/OysterClips/preferences.json}). Returns
     * {@link #defaults()} if the file is missing or unreadable.
     */
    public static RecorderPreferences loadDefault() {
        String home = System.getProperty("user.home");
        Path p = Paths.get(home, "Documents", "OysterClips", "preferences.json");
        return loadFrom(p);
    }

    /**
     * Read preferences from an explicit path. Suitable for tests on machines
     * without an {@code ~/Documents} directory (Linux CI, etc).
     */
    public static RecorderPreferences loadFrom(Path path) {
        RecorderPreferences d = defaults();
        if (path == null || !Files.exists(path) || !Files.isRegularFile(path)) {
            return d;
        }
        String content;
        try {
            content = Files.readString(path, StandardCharsets.UTF_8);
        } catch (IOException e) {
            return d;
        }
        return new RecorderPreferences(
            findBool(content, "enable_real_depth_shader", d.enableRealDepthShader),
            findFloat(content, "depth_near_metres", d.nearMetres),
            findFloat(content, "depth_far_metres", d.farMetres),
            findBool(content, "depth_reversed_z", d.reversedZ),
            findInt(content, "depth_width", d.width),
            findInt(content, "depth_height", d.height),
            findInt(content, "depth_fps", d.fps)
        );
    }

    // --- Minimal regex-style JSON scanning. Keys are quoted; values are
    // matched only when they're a clear primitive (no nested objects, no
    // arrays). Comments and whitespace inside the value region are not
    // supported — keep preferences.json a flat dict.

    static boolean findBool(String json, String key, boolean def) {
        int idx = findKey(json, key);
        if (idx < 0) return def;
        String val = tokenAfter(json, idx);
        if (val == null) return def;
        if ("true".equalsIgnoreCase(val)) return true;
        if ("false".equalsIgnoreCase(val)) return false;
        return def;
    }

    static float findFloat(String json, String key, float def) {
        int idx = findKey(json, key);
        if (idx < 0) return def;
        String val = tokenAfter(json, idx);
        if (val == null) return def;
        try {
            return Float.parseFloat(val);
        } catch (NumberFormatException e) {
            return def;
        }
    }

    static int findInt(String json, String key, int def) {
        int idx = findKey(json, key);
        if (idx < 0) return def;
        String val = tokenAfter(json, idx);
        if (val == null) return def;
        try {
            return Integer.parseInt(val);
        } catch (NumberFormatException e) {
            try {
                // accept "1920.0" as 1920
                return (int) Float.parseFloat(val);
            } catch (NumberFormatException ee) {
                return def;
            }
        }
    }

    private static int findKey(String json, String key) {
        String needle = "\"" + key + "\"";
        int k = json.indexOf(needle);
        if (k < 0) return -1;
        int c = json.indexOf(':', k + needle.length());
        if (c < 0) return -1;
        return c + 1;
    }

    private static String tokenAfter(String json, int start) {
        int n = json.length();
        int i = start;
        while (i < n && Character.isWhitespace(json.charAt(i))) i++;
        if (i >= n) return null;
        StringBuilder sb = new StringBuilder();
        while (i < n) {
            char c = json.charAt(i);
            if (c == ',' || c == '}' || c == ']' || c == '\n' || c == '\r') break;
            if (Character.isWhitespace(c)) break;
            sb.append(c);
            i++;
        }
        String out = sb.toString();
        return out.isEmpty() ? null : out;
    }
}
