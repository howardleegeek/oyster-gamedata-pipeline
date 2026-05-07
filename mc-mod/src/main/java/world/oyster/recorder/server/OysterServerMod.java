package world.oyster.recorder.server;

import net.fabricmc.api.DedicatedServerModInitializer;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerTickEvents;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Server-side companion to {@link world.oyster.recorder.OysterRecorderMod}.
 *
 * <p>Howard 2026-05-07 (D16): Pipeline 2 (cluster Mineflayer bots → buyer-
 * spec tarball) runs against headless Paper servers. The client mod can't
 * help here — bots aren't real clients with rendering. This server-side
 * entrypoint hooks {@link ServerTickEvents#END_SERVER_TICK} and dumps
 * REAL game state for every connected {@link
 * net.minecraft.server.network.ServerPlayerEntity} into per-player JSONL
 * files the cluster's {@code buyer_spec_pipeline.sh} consumes when packing
 * tarballs.
 *
 * <p>Differences from the client mod:
 * <ul>
 *   <li>Tick event source is server-side, not client-side
 *   <li>One {@link ServerStateCapture} runs across MANY players (the
 *       cluster runs ~9 bots simultaneously across 3 Paper instances)
 *   <li>Output path is server-relative (one mod jar handles many sessions)
 *   <li>Single canonical schema — {@code GameStateSample} from the parent
 *       package is reused unchanged
 * </ul>
 *
 * <p>Same fail-soft contract: any error logs and continues — never crash
 * the Paper server (which would take 9 bots offline at once).
 */
public class OysterServerMod implements DedicatedServerModInitializer {

    public static final Logger LOGGER = LoggerFactory.getLogger("oyster-recorder-server");

    private ServerStateCapture capture;

    @Override
    public void onInitializeServer() {
        LOGGER.info("Oyster Recorder server-side mod loading — will stream "
            + "real game state to <server>/oyster_state/<player>.jsonl");

        try {
            this.capture = new ServerStateCapture();
        } catch (Exception e) {
            LOGGER.error("Failed to initialise server-side capture; mod inert", e);
            return;
        }

        ServerTickEvents.END_SERVER_TICK.register(server -> {
            try {
                capture.tick(server);
            } catch (Exception e) {
                LOGGER.warn("Server tick capture failed (non-fatal): {}",
                    e.getMessage());
            }
        });

        LOGGER.info("Oyster Recorder server-side mod ready — tick listener registered");
    }
}
