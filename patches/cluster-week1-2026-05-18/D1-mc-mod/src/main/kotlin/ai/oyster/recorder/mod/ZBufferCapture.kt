package ai.oyster.recorder.mod

import net.fabricmc.api.ClientModInitializer
import net.fabricmc.fabric.api.client.rendering.v1.WorldRenderEvents
import net.minecraft.client.MinecraftClient
import org.lwjgl.opengl.GL11
import org.slf4j.LoggerFactory
import java.io.File
import java.io.FileOutputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.IntBuffer
import java.util.concurrent.CompletableFuture
import java.util.concurrent.ConcurrentLinkedQueue
import java.util.concurrent.atomic.AtomicInteger

/**
 * ZBufferCapture — captures GL depth buffer each frame, linearizes to world-space meters,
 * and writes raw f32 LE binary files for downstream EXR conversion (D2 Python pipeline).
 *
 * Controlled by env var OYSTER_ZBUFFER_CAPTURE=1 (default off to avoid impacting existing users).
 *
 * Output format per tick file:
 *   Header (12 bytes): u32 width (LE) | u32 height (LE) | u32 tick_id (LE)
 *   Payload: width × height × 4 bytes of f32 LE depth values (meters)
 *
 * Async write via CompletableFuture with bounded queue (max 60 pending).
 * Oldest entries are dropped when queue is full.
 */
object ZBufferCapture : ClientModInitializer {

    private val LOGGER = LoggerFactory.getLogger(ZBufferCapture::class.java)

    private const val ENV_VAR = "OYSTER_ZBUFFER_CAPTURE"
    private const val MAX_PENDING = 60

    // Bounded queue for async write tasks
    private val pendingWrites = ConcurrentLinkedQueue<PendingWrite>()
    private val pendingCount = AtomicInteger(0)

    // Tick counter
    private val tickCounter = AtomicInteger(0)

    // Reusable depth buffer (allocated once, resized if needed)
    @Volatile
    private var depthBuffer: FloatArray? = null
    @Volatile
    private var lastWidth = 0
    @Volatile
    private var lastHeight = 0

    /**
     * Data class holding everything needed for an async write.
     */
    private data class PendingWrite(
        val tickId: Int,
        val width: Int,
        val height: Int,
        val depthValues: FloatArray,
    )

    /**
     * Entry point called by Fabric Loader via fabric.mod.json client entrypoint.
     */
    @JvmStatic
    fun init() {
        val instance = ZBufferCapture
        instance.onInitializeClient()
    }

    override fun onInitializeClient() {
        val enabled = System.getenv(ENV_VAR) == "1"
        if (!enabled) {
            LOGGER.info("[ZBufferCapture] Disabled (set $ENV_VAR=1 to enable)")
            return
        }

        LOGGER.info("[ZBufferCapture] Enabled — hooking WorldRenderEvents.AFTER_TRANSLUCENT")

        WorldRenderEvents.AFTER_TRANSLUCENT.register { context ->
            try {
                captureFrame(context)
            } catch (e: Exception) {
                LOGGER.warn("[ZBufferCapture] Frame capture failed: ${e.message}", e)
            }
        }
    }

    /**
     * Main capture logic called each frame from the render thread.
     * Reads depth buffer, linearizes, queues async write.
     */
    private fun captureFrame(context: WorldRenderEvents.Context) {
        val client = MinecraftClient.getInstance()
        val camera = client.gameRenderer.camera

        if (camera == null) {
            return
        }

        val framebuffer = client.framebuffer
        if (framebuffer == null) {
            return
        }

        val width = framebuffer.textureWidth
        val height = framebuffer.textureHeight

        if (width <= 0 || height <= 0) {
            return
        }

        // Ensure depth buffer is allocated
        ensureDepthBuffer(width, height)

        val buf = depthBuffer!!

        // Read depth component as float from the current framebuffer
        // GL_DEPTH_COMPONENT + GL_FLOAT reads the depth buffer into a float array
        readDepthBuffer(width, height, buf)

        // Linearize depth values from [0,1] NDC to world-space meters
        val nearPlane = camera.clipStart.toDouble()
        val farPlane = camera.clipEnd.toDouble()

        if (nearPlane <= 0.0 || farPlane <= nearPlane) {
            LOGGER.debug("[ZBufferCapture] Invalid clip planes: near=$nearPlane far=$farPlane, skipping")
            return
        }

        linearizeDepth(buf, nearPlane, farPlane)

        // Queue async write
        val tickId = tickCounter.incrementAndGet()
        val writeData = PendingWrite(tickId, width, height, buf.copyOf())
        queueWrite(writeData)
    }

    /**
     * Ensures the reusable depth buffer is large enough for the given dimensions.
     */
    private fun ensureDepthBuffer(width: Int, height: Int) {
        val needed = width * height
        val current = depthBuffer
        if (current == null || current.size < needed || lastWidth != width || lastHeight != height) {
            depthBuffer = FloatArray(needed)
            lastWidth = width
            lastHeight = height
            LOGGER.debug("[ZBufferCapture] Allocated depth buffer: ${width}x${height} (${needed} floats)")
        }
    }

    /**
     * Reads the depth buffer from OpenGL into the provided FloatArray.
     * Uses GL11.glReadPixels with GL_DEPTH_COMPONENT and GL_FLOAT.
     */
    private fun readDepthBuffer(width: Int, height: Int, out: FloatArray) {
        // Allocate a direct ByteBuffer for the read
        val byteSize = width * height * 4 // 4 bytes per float
        val byteBuffer = ByteBuffer.allocateDirect(byteSize).order(ByteOrder.LITTLE_ENDIAN)
        val floatBuffer = byteBuffer.asFloatBuffer()

        // glReadPixels reads from bottom-left, so we need to flip vertically
        // Read into the buffer
        GL11.glReadPixels(0, 0, width, height, GL11.GL_DEPTH_COMPONENT, GL11.GL_FLOAT, byteBuffer)

        // Copy to our array, flipping Y axis (OpenGL origin is bottom-left, we want top-left)
        for (y in 0 until height) {
            val srcRow = (height - 1 - y) * width
            val dstRow = y * width
            for (x in 0 until width) {
                out[dstRow + x] = floatBuffer.get(srcRow + x)
            }
        }
    }

    /**
     * Linearizes depth buffer values from [0,1] range to world-space meters.
     *
     * Minecraft uses a perspective projection. The depth buffer value z_buf is
     * non-linear due to perspective division. We reverse the projection:
     *
     *   z_ndc = 2.0 * z_buf - 1.0          (convert [0,1] to [-1,1])
     *   z_eye = (2 * near * far) / (far + near - z_ndc * (far - near))
     *   z_world = |z_eye|                    (distance from camera in meters)
     *
     * This is the standard OpenGL depth linearization formula.
     */
    fun linearizeDepth(depthValues: FloatArray, near: Double, far: Double) {
        val nearF = near.toFloat()
        val farF = far.toFloat()
        val twoNearFar = 2.0f * nearF * farF
        val farPlusNear = farF + nearF
        val farMinusNear = farF - nearF

        for (i in depthValues.indices) {
            val zBuf = depthValues[i]
            // Convert from [0,1] to [-1,1] NDC
            val zNdc = 2.0f * zBuf - 1.0f
            // Reverse perspective projection to get eye-space Z
            val zEye = twoNearFar / (farPlusNear - zNdc * farMinusNear)
            // World-space distance (absolute value, since eye-space Z is negative in OpenGL)
            depthValues[i] = kotlin.math.abs(zEye)
        }
    }

    /**
     * Queues a write task. If the queue is full (>= MAX_PENDING), drops the oldest entry.
     */
    private fun queueWrite(data: PendingWrite) {
        // Drop oldest if queue is full
        while (pendingCount.get() >= MAX_PENDING) {
            val dropped = pendingWrites.poll()
            if (dropped != null) {
                pendingCount.decrementAndGet()
                LOGGER.warn("[ZBufferCapture] Queue full, dropping oldest tick ${dropped.tickId}")
            } else {
                break
            }
        }

        pendingWrites.offer(data)
        pendingCount.incrementAndGet()

        // Fire async write
        CompletableFuture.runAsync {
            try {
                writeToFile(data)
            } catch (e: Exception) {
                LOGGER.error("[ZBufferCapture] Async write failed for tick ${data.tickId}: ${e.message}", e)
            } finally {
                pendingWrites.remove(data)
                pendingCount.decrementAndGet()
            }
        }
    }

    /**
     * Writes the depth data to disk in the specified binary format.
     *
     * Path: ~/Documents/OysterClips/active_session/zbuffer/tick_<N>.bin
     *
     * Format:
     *   Bytes 0-3:   u32 width (little-endian)
     *   Bytes 4-7:   u32 height (little-endian)
     *   Bytes 8-11:  u32 tick_id (little-endian)
     *   Bytes 12+:   raw f32 LE depth values (width × height × 4 bytes)
     */
    private fun writeToFile(data: PendingWrite) {
        val outputDir = getOutputDirectory()
        outputDir.mkdirs()

        val outputFile = File(outputDir, "tick_${data.tickId}.bin")

        FileOutputStream(outputFile).use { fos ->
            val header = ByteBuffer.allocate(12).order(ByteOrder.LITTLE_ENDIAN)
            header.putInt(data.width)
            header.putInt(data.height)
            header.putInt(data.tickId)
            header.flip()
            fos.write(header.array())

            // Write depth values as little-endian f32
            val byteBuffer = ByteBuffer.allocate(data.depthValues.size * 4).order(ByteOrder.LITTLE_ENDIAN)
            byteBuffer.asFloatBuffer().put(data.depthValues)
            fos.write(byteBuffer.array())
        }

        LOGGER.debug("[ZBufferCapture] Wrote ${outputFile.name} (${data.width}x${data.height}, ${data.depthValues.size} floats)")
    }

    /**
     * Returns the output directory for Z-buffer captures.
     * ~/Documents/OysterClips/active_session/zbuffer/
     */
    private fun getOutputDirectory(): File {
        val userHome = System.getProperty("user.home")
        return File(userHome, "Documents/OysterClips/active_session/zbuffer")
    }

    /**
     * Returns the current pending write count (for testing/monitoring).
     */
    fun getPendingCount(): Int = pendingCount.get()

    /**
     * Returns whether Z-buffer capture is enabled.
     */
    fun isEnabled(): Boolean = System.getenv(ENV_VAR) == "1"

    /**
     * Resets the tick counter (for testing).
     */
    fun resetTickCounter() {
        tickCounter.set(0)
    }
}
