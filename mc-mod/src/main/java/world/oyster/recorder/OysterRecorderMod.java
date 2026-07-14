package world.oyster.recorder;

import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import world.oyster.recorder.depth.RealDepthExporter;
import world.oyster.recorder.depth.RecorderPreferences;

/**
 * Oyster Recorder Mod — Fabric ClientModInitializer.
 *
 * <p>Howard 2026-05-07: this mod's single job is to make the
 * {@code action_camera.json} fields in the recorder tarball REAL instead of
 * placeholder. Without it, OysterRecorder.exe fills {@code camera_position},
 * {@code player_position}, {@code player_rotation_oula}, etc. with constants
 * (the [0.0, 64.0, 0.0] sentinel). With it, those fields reflect actual
 * per-tick player state.
 *
 * <p>Architecture:
 * <ul>
 *   <li>Hooks {@link ClientTickEvents#END_CLIENT_TICK} (≈20 Hz at vanilla TPS)
 *   <li>Captures {@link GameStateSample} per tick from the client player
 *   <li>{@link JsonlWriter} flushes to disk in append mode every tick
 *   <li>Output path resolved by {@link SessionDir} — same convention the
 *       Python recorder reads from
 * </ul>
 *
 * <p>Fail-soft: if anything goes wrong (no player, no Documents dir, IO error)
 * we log and skip — never crash the user's game. The recorder's fallback to
 * placeholder kicks in transparently.
 */
public class OysterRecorderMod implements ClientModInitializer {

    public static final String MOD_ID = "oyster-recorder";
    public static final Logger LOGGER = LoggerFactory.getLogger(MOD_ID);

    /** Single global capture state; OK for client-side singleton mod. */
    private GameStateCapture capture;

    /**
     * G198 real-depth exporter — opt-in via
     * {@code ~/Documents/OysterClips/preferences.json} flag
     * {@code enable_real_depth_shader=true}. When inactive the recorder's
     * existing DepthAnything V2 fallback runs unmodified.
     */
    private RealDepthExporter depthExporter;

    @Override
    public void onInitializeClient() {
        LOGGER.info("Oyster Recorder mod loading — will stream real game state to {}",
            SessionDir.outputPath());

        try {
            this.capture = new GameStateCapture(new JsonlWriter(SessionDir.outputPath()));
        } catch (Exception e) {
            LOGGER.error("Failed to initialise game-state writer; mod will be inert", e);
            return;
        }

        ClientTickEvents.END_CLIENT_TICK.register(client -> {
            if (capture == null) return;
            try {
                capture.tick(client);
            } catch (Exception e) {
                // never let our exception crash the user's game
                LOGGER.warn("Tick capture failed (non-fatal): {}", e.getMessage());
            }
        });

        // G198: real-depth shader path. Loads preferences from the same
        // OysterClips/ directory; defaults to disabled so DA-V2 stays the
        // canonical pipeline until vendors explicitly opt-in.
        try {
            RecorderPreferences prefs = RecorderPreferences.loadDefault();
            this.depthExporter = new RealDepthExporter(prefs, SessionDir.depthDir());
            this.depthExporter.start();
        } catch (Throwable t) {
            LOGGER.warn("RealDepthExporter init failed; DA-V2 fallback remains active: {}",
                t.toString());
        }

        LOGGER.info("Oyster Recorder mod ready — tick listener registered");
    }
}
