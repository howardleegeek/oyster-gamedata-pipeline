package world.oyster.recorder;

import net.fabricmc.fabric.api.client.rendering.v1.WorldRenderEvents;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.gl.Framebuffer;
import org.lwjgl.opengl.GL11;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.FloatBuffer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * ZBufferCapture — per-frame GL depth-buffer readback for the Oyster Recorder mod.
 *
 * <p>Companion to {@link GameStateCapture}: where that streams player pose to
 * {@code game_state.jsonl}, this streams the raw GL depth buffer to
 * {@code zbuffer/tick_<N>.bin} so the downstream (D2) pipeline can build
 * metric depth / EXR. Per Howard's directive depth is RAW data: we write the
 * unmodified GL depth-component values ([0,1], non-linear) and linearise to
 * world-space metres in post-processing (where near/far are known from
 * metadata) rather than guessing version-specific camera-projection APIs here.
 *
 * <p>Capture is throttled to {@code OYSTER_ZBUFFER_FPS} (default 6) to align
 * with the 6-FPS video/game_state cadence (prd_test_depth_6fps_alignment) and
 * to keep disk throughput sane (a 1080p depth frame is ~8 MB raw).
 *
 * <p>Output co-locates with game_state via {@link SessionDir}: the
 * {@code zbuffer/} dir is created next to {@code game_state.jsonl} (honouring
 * {@code OYSTER_SESSION_DIR} when the launcher sets it). A sidecar
 * {@code zbuffer/index.jsonl} records {tick, t_ms, w, h} per frame for
 * downstream temporal alignment.
 *
 * <p>Fail-soft: any error logs and skips — never crashes the user's game.
 * Disable entirely with {@code OYSTER_ZBUFFER_CAPTURE=0}.
 */
public final class ZBufferCapture {

    private static final Logger LOGGER = LoggerFactory.getLogger("oyster-recorder/zbuffer");

    /** "0" disables; any other value (or unset) leaves capture ON for this depth build. */
    private static final String ENV_ENABLE = "OYSTER_ZBUFFER_CAPTURE";
    /** Capture rate in frames/sec; default 6 to match the video/game_state alignment cadence. */
    private static final String ENV_FPS = "OYSTER_ZBUFFER_FPS";
    /** Bounded async write queue — frames are dropped (logged) rather than stalling the render thread. */
    private static final int MAX_PENDING = 16;

    private static final AtomicInteger tickCounter = new AtomicInteger(0);
    private static final AtomicInteger pendingCount = new AtomicInteger(0);
    private static final Object INDEX_LOCK = new Object();

    private static volatile long intervalMs = 167L; // ~6 FPS
    private static volatile long lastCaptureMs = 0L;

    // Reusable direct buffer for glReadPixels (touched on render thread only).
    private static ByteBuffer readBuf = null;
    private static int readBufCapacity = 0;

    private ZBufferCapture() {}

    /** Registers the per-frame depth hook. Called from OysterRecorderMod.onInitializeClient(). */
    public static void register() {
        if ("0".equals(System.getenv(ENV_ENABLE))) {
            LOGGER.info("[ZBufferCapture] disabled (OYSTER_ZBUFFER_CAPTURE=0)");
            return;
        }
        int fps = 6;
        try {
            String f = System.getenv(ENV_FPS);
            if (f != null && !f.trim().isEmpty()) {
                fps = Math.max(1, Math.min(60, Integer.parseInt(f.trim())));
            }
        } catch (NumberFormatException ignored) {
            // keep default
        }
        intervalMs = Math.max(1L, 1000L / fps);

        LOGGER.info("[ZBufferCapture] enabled — {} FPS, raw f32 depth → {}", fps, zbufferDir());

        WorldRenderEvents.AFTER_TRANSLUCENT.register(context -> {
            try {
                maybeCapture();
            } catch (Throwable t) {
                LOGGER.warn("[ZBufferCapture] frame capture failed (non-fatal): {}", t.toString());
            }
        });
    }

    private static void maybeCapture() {
        long now = System.currentTimeMillis();
        if (now - lastCaptureMs < intervalMs) return;

        MinecraftClient client = MinecraftClient.getInstance();
        if (client == null || client.world == null || client.player == null) return; // only in-world
        Framebuffer fb = client.getFramebuffer();
        if (fb == null) return;
        int width = fb.textureWidth;
        int height = fb.textureHeight;
        if (width <= 0 || height <= 0) return;

        lastCaptureMs = now;

        int floats = width * height;
        int bytes = floats * 4;
        if (readBuf == null || readBufCapacity < bytes) {
            readBuf = ByteBuffer.allocateDirect(bytes).order(ByteOrder.LITTLE_ENDIAN);
            readBufCapacity = bytes;
        }
        readBuf.clear();

        // Raw depth component of the currently-bound (world) framebuffer.
        GL11.glReadPixels(0, 0, width, height, GL11.GL_DEPTH_COMPONENT, GL11.GL_FLOAT, readBuf);

        // Copy into a heap float[] with vertical flip (GL origin is bottom-left → top-left).
        FloatBuffer src = readBuf.asFloatBuffer();
        float[] depth = new float[floats];
        for (int y = 0; y < height; y++) {
            int srcRow = (height - 1 - y) * width;
            int dstRow = y * width;
            for (int x = 0; x < width; x++) {
                depth[dstRow + x] = src.get(srcRow + x);
            }
        }

        int tickId = tickCounter.incrementAndGet();
        enqueueWrite(tickId, width, height, now, depth);
    }

    private static void enqueueWrite(int tickId, int width, int height, long tMs, float[] depth) {
        if (pendingCount.get() >= MAX_PENDING) {
            LOGGER.warn("[ZBufferCapture] write queue full ({}), dropping tick {}", MAX_PENDING, tickId);
            return;
        }
        pendingCount.incrementAndGet();
        CompletableFuture.runAsync(() -> {
            try {
                writeTick(tickId, width, height, tMs, depth);
            } catch (Throwable t) {
                LOGGER.warn("[ZBufferCapture] async write failed for tick {}: {}", tickId, t.toString());
            } finally {
                pendingCount.decrementAndGet();
            }
        });
    }

    private static void writeTick(int tickId, int width, int height, long tMs, float[] depth) throws IOException {
        Path dir = zbufferDir();
        Files.createDirectories(dir);
        Path out = dir.resolve("tick_" + tickId + ".bin");

        ByteBuffer buf = ByteBuffer.allocate(12 + depth.length * 4).order(ByteOrder.LITTLE_ENDIAN);
        buf.putInt(width);
        buf.putInt(height);
        buf.putInt(tickId);
        for (float v : depth) buf.putFloat(v);
        Files.write(out, buf.array(),
            StandardOpenOption.CREATE, StandardOpenOption.WRITE, StandardOpenOption.TRUNCATE_EXISTING);

        // Sidecar alignment index (one JSON object per line).
        String line = "{\"tick\":" + tickId + ",\"t_ms\":" + tMs
            + ",\"w\":" + width + ",\"h\":" + height
            + ",\"file\":\"tick_" + tickId + ".bin\",\"format\":\"raw_gl_depth_f32le\"}\n";
        synchronized (INDEX_LOCK) {
            Files.write(dir.resolve("index.jsonl"), line.getBytes(StandardCharsets.UTF_8),
                StandardOpenOption.CREATE, StandardOpenOption.WRITE, StandardOpenOption.APPEND);
        }
    }

    /** zbuffer/ dir co-located with game_state.jsonl (honours OYSTER_SESSION_DIR). */
    private static Path zbufferDir() {
        Path gs = SessionDir.outputPath();          // .../game_state.jsonl
        Path parent = gs.getParent();
        if (parent == null) parent = gs;
        return parent.resolve("zbuffer");
    }
}
