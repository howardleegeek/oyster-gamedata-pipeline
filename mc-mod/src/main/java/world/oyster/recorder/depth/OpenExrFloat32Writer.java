package world.oyster.recorder.depth;

import java.io.IOException;
import java.io.OutputStream;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;

/**
 * Minimal pure-Java OpenEXR 2.0 writer for single-channel float32 "Z"
 * scanline images. Produces files that pass the existing
 * {@code _read_exr_lazy} structural check in
 * {@code src/oyster_agent_runner/lint/lint_buyer_spec.py} and the lint v3
 * {@code _check_depth_ratio} OpenEXR-based read in
 * {@code bin/lint_v3_prd_grounded.py}.
 *
 * <p>Howard 2026-05-13 (G198): a Fabric client mod must not pull in 1.5 MB
 * of native OpenEXR JNI. The whole file format is ~200 lines of pure I/O
 * once you commit to NO_COMPRESSION + scanline layout, so we do it inline
 * and never touch the runtime classloader for anything outside the JDK.
 *
 * <h2>File format (OpenEXR 2.0, §"Technical Introduction")</h2>
 * <ol>
 *   <li>Magic: {@code 0x01 0x31 0x2F 0x76} (little-endian uint32)</li>
 *   <li>Version: {@code 2} (24-bit version, top byte is single-tile flag = 0)</li>
 *   <li>Header attributes (each: name\0 type\0 size(int32) data) terminated by \0:
 *     <ul>
 *       <li>{@code channels}: chlist with one entry, name="Z", pixelType=FLOAT(2),
 *           pLinear=0, reserved 3 bytes, xSampling=1, ySampling=1; terminated by \0</li>
 *       <li>{@code compression}: uint8 0 (NO_COMPRESSION)</li>
 *       <li>{@code dataWindow}: box2i (xMin yMin xMax yMax int32 each)</li>
 *       <li>{@code displayWindow}: box2i</li>
 *       <li>{@code lineOrder}: uint8 0 (INCREASING_Y)</li>
 *       <li>{@code pixelAspectRatio}: float32 1.0</li>
 *       <li>{@code screenWindowCenter}: v2f (0.0, 0.0)</li>
 *       <li>{@code screenWindowWidth}: float32 1.0</li>
 *     </ul>
 *   </li>
 *   <li>Line offset table: uint64[h] giving absolute file offset of each scanline block</li>
 *   <li>Scanline blocks, top to bottom: int32 yCoord, int32 pixelDataSize, float32 pixels (width*4 bytes)</li>
 * </ol>
 *
 * <p>All multi-byte fields are little-endian (OpenEXR convention).
 */
public final class OpenExrFloat32Writer {

    /** OpenEXR magic number, little-endian. */
    private static final int MAGIC = 0x01312F76;
    /** Version 2, scanline-image flag = 0. */
    private static final int VERSION = 2;

    private OpenExrFloat32Writer() {}

    /**
     * Write a float32 depth buffer as a single-channel "Z" OpenEXR file.
     *
     * @param outPath absolute path of the output .exr file. Parent dir
     *                will be created if missing. Any existing file is
     *                truncated.
     * @param depthRowMajor float32 values in row-major order (top-to-bottom
     *                      scanline 0..height-1, each scanline left-to-right
     *                      0..width-1). Length must equal width*height.
     * @param width  image width in pixels.
     * @param height image height in pixels.
     * @throws IOException on filesystem error.
     * @throws IllegalArgumentException if the buffer length doesn't match
     *         width*height, or if width/height are non-positive.
     */
    public static void write(Path outPath, float[] depthRowMajor, int width, int height)
        throws IOException {
        if (width <= 0 || height <= 0) {
            throw new IllegalArgumentException(
                "non-positive dimensions: " + width + "x" + height);
        }
        if ((long) depthRowMajor.length != (long) width * height) {
            throw new IllegalArgumentException(
                "buffer length " + depthRowMajor.length
                    + " != width*height " + ((long) width * height));
        }

        Files.createDirectories(outPath.getParent());

        try (OutputStream raw = Files.newOutputStream(outPath,
                StandardOpenOption.CREATE,
                StandardOpenOption.TRUNCATE_EXISTING,
                StandardOpenOption.WRITE)) {
            writeStream(raw, depthRowMajor, width, height);
        }
    }

    /**
     * Stream-oriented variant; useful for tests that write into a
     * ByteArrayOutputStream and assert against the binary.
     */
    public static void writeStream(OutputStream raw, float[] depthRowMajor,
                                   int width, int height) throws IOException {
        // Build the header first (we need its byte length to compute the
        // line offset table values).
        byte[] header = buildHeader(width, height);

        // Each scanline block = 8 bytes (y int + size int) + width*4 bytes
        // (float pixels). With NO_COMPRESSION the size field is always
        // width*4.
        long magicVersionHeaderLen = 4L + 4L + header.length;
        long offsetTableLen = (long) height * 8L;
        long firstScanlineOffset = magicVersionHeaderLen + offsetTableLen;
        int scanlineBytes = width * 4;
        int scanlineBlockBytes = 8 + scanlineBytes;

        // Magic + version (little-endian).
        raw.write(le32(MAGIC));
        raw.write(le32(VERSION));

        // Header.
        raw.write(header);

        // Line-offset table — absolute file offsets of each scanline block.
        ByteBuffer offsets = ByteBuffer
            .allocate((int) offsetTableLen)
            .order(ByteOrder.LITTLE_ENDIAN);
        for (int y = 0; y < height; y++) {
            offsets.putLong(firstScanlineOffset + (long) y * scanlineBlockBytes);
        }
        raw.write(offsets.array());

        // Scanline blocks.
        ByteBuffer pixelBuf = ByteBuffer
            .allocate(scanlineBytes)
            .order(ByteOrder.LITTLE_ENDIAN);
        for (int y = 0; y < height; y++) {
            raw.write(le32(y));
            raw.write(le32(scanlineBytes));
            pixelBuf.clear();
            int rowStart = y * width;
            for (int x = 0; x < width; x++) {
                pixelBuf.putFloat(depthRowMajor[rowStart + x]);
            }
            raw.write(pixelBuf.array());
        }
        raw.flush();
    }

    // ---------------------------------------------------------------- header

    private static byte[] buildHeader(int width, int height) throws IOException {
        java.io.ByteArrayOutputStream buf = new java.io.ByteArrayOutputStream(256);

        // channels: chlist with one entry "Z"
        // chlist entry: name\0 + pixelType(int32) + pLinear(uint8) + reserved[3] + xSampling(int32) + ySampling(int32)
        java.io.ByteArrayOutputStream chlist = new java.io.ByteArrayOutputStream(32);
        chlist.write("Z".getBytes("UTF-8"));
        chlist.write(0);                         // name terminator
        chlist.write(le32(2));                   // FLOAT
        chlist.write(0);                         // pLinear
        chlist.write(0); chlist.write(0); chlist.write(0); // reserved
        chlist.write(le32(1));                   // xSampling
        chlist.write(le32(1));                   // ySampling
        chlist.write(0);                         // chlist terminator
        writeAttr(buf, "channels", "chlist", chlist.toByteArray());

        // compression: NO_COMPRESSION (0)
        writeAttr(buf, "compression", "compression", new byte[] { 0 });

        // dataWindow + displayWindow: box2i (0, 0, width-1, height-1)
        byte[] box = box2i(0, 0, width - 1, height - 1);
        writeAttr(buf, "dataWindow", "box2i", box);
        writeAttr(buf, "displayWindow", "box2i", box);

        // lineOrder: INCREASING_Y
        writeAttr(buf, "lineOrder", "lineOrder", new byte[] { 0 });

        // pixelAspectRatio: 1.0
        writeAttr(buf, "pixelAspectRatio", "float", floatLE(1.0f));

        // screenWindowCenter: (0, 0)
        ByteBuffer ctr = ByteBuffer.allocate(8).order(ByteOrder.LITTLE_ENDIAN);
        ctr.putFloat(0.0f); ctr.putFloat(0.0f);
        writeAttr(buf, "screenWindowCenter", "v2f", ctr.array());

        // screenWindowWidth: 1.0
        writeAttr(buf, "screenWindowWidth", "float", floatLE(1.0f));

        // Header terminator: a single null byte.
        buf.write(0);
        return buf.toByteArray();
    }

    private static void writeAttr(java.io.ByteArrayOutputStream out,
                                  String name, String type, byte[] data) throws IOException {
        out.write(name.getBytes("UTF-8"));
        out.write(0);
        out.write(type.getBytes("UTF-8"));
        out.write(0);
        out.write(le32(data.length));
        out.write(data);
    }

    private static byte[] le32(int v) {
        return ByteBuffer.allocate(4).order(ByteOrder.LITTLE_ENDIAN).putInt(v).array();
    }

    private static byte[] floatLE(float v) {
        return ByteBuffer.allocate(4).order(ByteOrder.LITTLE_ENDIAN).putFloat(v).array();
    }

    private static byte[] box2i(int xMin, int yMin, int xMax, int yMax) {
        ByteBuffer b = ByteBuffer.allocate(16).order(ByteOrder.LITTLE_ENDIAN);
        b.putInt(xMin); b.putInt(yMin); b.putInt(xMax); b.putInt(yMax);
        return b.array();
    }
}
