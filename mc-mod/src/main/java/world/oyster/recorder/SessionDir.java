package world.oyster.recorder;

import java.nio.file.Path;
import java.nio.file.Paths;

/**
 * Resolves the JSONL output path the mod writes to and the recorder reads
 * from. Single source of truth so the contract between the two stays in sync.
 *
 * <p>Howard 2026-05-07: chose {@code ~/Documents/OysterClips/active_session/}
 * because:
 * <ul>
 *   <li>Same parent ({@code OysterClips}) the .exe already writes mp4s to —
 *       testers don't need to know about a second location
 *   <li>{@code active_session} is mtime-rotated by the .exe at packaging time:
 *       when packaging finishes, dir is moved to {@code clip-N/} and a fresh
 *       {@code active_session/} is created. So this mod always writes to the
 *       current recording's dir.
 *   <li>Cross-platform: {@code System.getProperty("user.home")} resolves to
 *       the right place on Windows / macOS / Linux without extra logic
 * </ul>
 *
 * <p>Note that {@code Documents} is the localised name on Windows; on macOS
 * it's the same. On Linux some distros may not have {@code ~/Documents/} —
 * we'll create it on first write.
 */
public final class SessionDir {

    private SessionDir() {}

    /**
     * Returns the canonical JSONL path. Format
     * {@code ~/Documents/OysterClips/active_session/game_state.jsonl}.
     */
    public static Path outputPath() {
        String home = System.getProperty("user.home");
        return Paths.get(home, "Documents", "OysterClips",
            "active_session", "game_state.jsonl");
    }

    /**
     * Returns the canonical depth/ directory the real-depth shader writes
     * EXR sidecars to. Same parent as {@link #outputPath()} so the recorder
     * picks them up at tarball-pack time alongside game_state.jsonl.
     *
     * <p>Format: {@code ~/Documents/OysterClips/active_session/depth/}.
     */
    public static Path depthDir() {
        String home = System.getProperty("user.home");
        return Paths.get(home, "Documents", "OysterClips",
            "active_session", "depth");
    }
}
