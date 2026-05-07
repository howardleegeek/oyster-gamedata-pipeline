package world.oyster.recorder;

import net.minecraft.client.MinecraftClient;
import net.minecraft.client.network.ClientPlayerEntity;
import net.minecraft.util.math.Vec3d;
import net.minecraft.world.GameMode;
import net.minecraft.world.World;

/**
 * Per-tick capture of MinecraftClient state.
 *
 * <p>Howard 2026-05-07: this is the heart of the mod. Each invocation of
 * {@link #tick(MinecraftClient)} reads the client player's position,
 * rotation, velocity, dimension, and on-ground flag, and asks the
 * {@link JsonlWriter} to persist it. The recorder reads the JSONL and
 * interpolates to whatever frame rate the buyer-spec action_camera demands
 * (currently 6 fps × 300 s = 1801 frames).
 *
 * <p>Skip conditions (mod stays inert):
 * <ul>
 *   <li>{@code client.player == null} — at title screen or loading
 *   <li>{@code client.world == null} — no world loaded yet
 *   <li>Spectator / creative gamemode — recorder doesn't want this; we
 *       still write but tag it so downstream can filter
 * </ul>
 */
public final class GameStateCapture {

    private final JsonlWriter writer;
    private long tickCounter = 0L;

    public GameStateCapture(JsonlWriter writer) {
        this.writer = writer;
    }

    public void tick(MinecraftClient client) {
        ClientPlayerEntity player = client.player;
        World world = client.world;
        if (player == null || world == null) {
            return;
        }

        // Position & rotation.
        Vec3d pos = player.getPos();
        float yaw = player.getYaw();        // -180..180, 0 = south
        float pitch = player.getPitch();    // -90..90,   negative = up
        Vec3d look = player.getRotationVec(1.0F);  // unit vector (look direction)
        Vec3d vel = player.getVelocity();   // blocks per tick

        // Game-mode + dimension.
        String dimension = world.getRegistryKey().getValue().toString();
        GameMode gameMode = client.interactionManager == null
            ? GameMode.SURVIVAL
            : (client.interactionManager.getCurrentGameMode() != null
                ? client.interactionManager.getCurrentGameMode()
                : GameMode.SURVIVAL);

        long now = System.currentTimeMillis();

        GameStateSample sample = new GameStateSample(
            tickCounter++,
            now,
            pos.x, pos.y, pos.z,
            yaw, pitch,
            look.x, look.y, look.z,
            vel.x, vel.y, vel.z,
            player.isOnGround(),
            player.isSneaking(),
            player.isSprinting(),
            dimension,
            gameMode.name()
        );

        writer.append(sample);
    }
}
