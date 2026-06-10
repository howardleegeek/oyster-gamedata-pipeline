package world.oyster.recorder.server;

import net.minecraft.server.MinecraftServer;
import net.minecraft.server.network.ServerPlayerEntity;
import net.minecraft.util.math.Vec3d;
import net.minecraft.world.GameMode;
import net.minecraft.world.World;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import world.oyster.recorder.GameStateSample;
import world.oyster.recorder.JsonlWriter;

import java.io.IOException;
import java.nio.file.Path;
import java.util.HashMap;
import java.util.Map;
import java.util.UUID;

/**
 * Server-side per-tick capture for ALL connected players.
 *
 * <p>Howard 2026-05-07 (D16): the cluster spawns 9+ Mineflayer bots
 * simultaneously across 3 Paper instances. Each is a {@link
 * ServerPlayerEntity} from the server's perspective. We maintain a {@link
 * Map} of {@link UUID} → {@link JsonlWriter} so each bot's stream goes
 * into its own file (cluster's {@code buyer_spec_pipeline.sh} reads them
 * by username at packing time).
 *
 * <p>Writers are created lazily on first tick the player is observed and
 * removed from the map when the player disconnects (we drop the entry
 * silently — the file stays for the cluster to consume; we don't truncate
 * post-hoc).
 *
 * <p>One-tick gaps when the player is in a loading screen or transferring
 * dimension are tolerated — we just skip that tick.
 */
public final class ServerStateCapture {

    private static final Logger LOGGER = LoggerFactory.getLogger(ServerStateCapture.class);

    /** Per-player writers, keyed by stable player UUID (not username, since
     *  username can theoretically change mid-session via name change). */
    private final Map<UUID, JsonlWriter> writers = new HashMap<>();

    /** Per-player monotonic tick counter. Mirrors the client-side capture's
     *  semantics so downstream tooling sees a single sequence per stream. */
    private final Map<UUID, Long> tickCounts = new HashMap<>();

    public void tick(MinecraftServer server) {
        if (server == null || server.getPlayerManager() == null) {
            return;
        }
        long now = System.currentTimeMillis();

        for (ServerPlayerEntity player : server.getPlayerManager().getPlayerList()) {
            captureOne(player, server, now);
        }

        // Best-effort: if a player has disconnected since last tick, drop
        // their writer entry. We don't close the file — the OS will when
        // the JVM exits or the writer becomes unreferenced and GC'd. The
        // cluster's pipeline reads the file at packing time, not live.
        writers.keySet().retainAll(currentUuids(server));
        tickCounts.keySet().retainAll(currentUuids(server));
    }

    private java.util.Set<UUID> currentUuids(MinecraftServer server) {
        java.util.Set<UUID> uuids = new java.util.HashSet<>();
        for (ServerPlayerEntity p : server.getPlayerManager().getPlayerList()) {
            uuids.add(p.getUuid());
        }
        return uuids;
    }

    private void captureOne(ServerPlayerEntity player,
                            MinecraftServer server,
                            long timestampMs) {
        UUID uuid = player.getUuid();
        World world = player.getWorld();
        if (world == null) return;

        JsonlWriter writer = writers.get(uuid);
        if (writer == null) {
            try {
                Path path = ServerSessionDir.outputPath(server, player.getName().getString());
                writer = new JsonlWriter(path);
                writers.put(uuid, writer);
                tickCounts.put(uuid, 0L);
                LOGGER.info("Started capture for player {} (uuid {}) at {}",
                    player.getName().getString(), uuid, path);
            } catch (IOException e) {
                LOGGER.warn("Could not initialise writer for {}: {}",
                    player.getName().getString(), e.getMessage());
                return;
            }
        }

        Vec3d pos = player.getPos();
        float yaw = player.getYaw();
        float pitch = player.getPitch();
        Vec3d look = player.getRotationVec(1.0F);
        Vec3d vel = player.getVelocity();
        String dimension = world.getRegistryKey().getValue().toString();
        GameMode gm = player.interactionManager == null
            ? GameMode.SURVIVAL
            : (player.interactionManager.getGameMode() != null
                ? player.interactionManager.getGameMode()
                : GameMode.SURVIVAL);

        long tick = tickCounts.get(uuid);
        tickCounts.put(uuid, tick + 1);

        GameStateSample sample = new GameStateSample(
            tick,
            timestampMs,
            pos.x, pos.y, pos.z,
            yaw, pitch,
            look.x, look.y, look.z,
            vel.x, vel.y, vel.z,
            player.isOnGround(),
            player.isSneaking(),
            player.isSprinting(),
            false,
            dimension,
            gm.name()
        );

        writer.append(sample);
    }
}
