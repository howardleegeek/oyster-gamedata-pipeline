package world.oyster.recorder;

import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

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

        LOGGER.info("Oyster Recorder mod ready — tick listener registered");

        // Z-buffer depth capture (raw GL depth → zbuffer/tick_<N>.bin). Independent
        // of game-state capture and fail-soft: a depth-hook failure must never stop
        // game_state streaming or crash the user's game.
        try {
            ZBufferCapture.register();
        } catch (Throwable t) {
            LOGGER.warn("Z-buffer depth capture init failed (non-fatal): {}", t.toString());
        }
    }
}
