package world.oyster.recorder;

import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.concurrent.atomic.AtomicBoolean;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Resolves the JSONL output path the mod writes to and the recorder reads
 * from. Single source of truth so the contract between the two stays in sync.
 *
 * <p>PRD-100 (2026-05-11): {@code OYSTER_SESSION_DIR} env var is the
 * primary signal. The launcher ({@code bin/oyster_launch_mc.py}) sets it
 * to the Rust recorder's {@code %LOCALAPPDATA%/GameData Recorder/recordings/<session_id>/}
 * before spawning the JVM, so the mod's {@code game_state.jsonl} lands in
 * the same session directory as the {@code action_camera.json}, video, and
 * other PRD artifacts.
 *
 * <p>Fallback (when env unset — manual testers / dev runs): legacy
 * {@code ~/Documents/OysterClips/active_session/} retained so existing
 * manual workflows don't regress.
 *
 * <p>Howard 2026-05-07 (historical context for fallback): chose
 * {@code ~/Documents/OysterClips/active_session/} because:
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

    private static final Logger LOGGER = LoggerFactory.getLogger(SessionDir.class);
    private static final String ENV_SESSION_DIR = "OYSTER_SESSION_DIR";
    private static final AtomicBoolean PATH_LOGGED = new AtomicBoolean(false);

    private SessionDir() {}

    /**
     * Returns the canonical JSONL path. PRD-100: prefers
     * {@code $OYSTER_SESSION_DIR/game_state.jsonl} so the file lands in the
     * Rust recorder's session directory. Falls back to
     * {@code ~/Documents/OysterClips/active_session/game_state.jsonl}
     * when the env var is unset (manual testers).
     */
    public static Path outputPath() {
        String envDir = System.getenv(ENV_SESSION_DIR);
        Path resolved;
        boolean fromEnv;
        if (envDir != null && !envDir.trim().isEmpty()) {
            resolved = Paths.get(envDir.trim(), "game_state.jsonl");
            fromEnv = true;
        } else {
            String home = System.getProperty("user.home");
            resolved = Paths.get(home, "Documents", "OysterClips",
                "active_session", "game_state.jsonl");
            fromEnv = false;
        }
        if (PATH_LOGGED.compareAndSet(false, true)) {
            LOGGER.info("OysterRecorder mod: game_state.jsonl path = {} (source={})",
                resolved,
                fromEnv ? "OYSTER_SESSION_DIR env" : "fallback ~/Documents/OysterClips/active_session");
        }
        return resolved;
    }
}
