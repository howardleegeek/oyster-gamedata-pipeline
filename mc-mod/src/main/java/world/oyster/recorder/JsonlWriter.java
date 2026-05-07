package world.oyster.recorder;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;

/**
 * Append-only JSONL writer with atomic per-line semantics.
 *
 * <p>Howard 2026-05-07: simple over fancy — open the file once at construct
 * time, append each line synchronously. The recorder will only consume the
 * file at tarball-pack time (long after MC has exited) so we don't need
 * memory-mapped or streaming reads. Single writer assumed (mod is client-
 * side singleton).
 *
 * <p>Robustness:
 * <ul>
 *   <li>Output path's parent dir created if missing
 *   <li>Existing file is truncated at construction (one session = one file;
 *       OysterRecorder.exe rotates the active_session/ dir per recording)
 *   <li>Writes are flushed (StandardOpenOption.SYNC) so a Minecraft crash
 *       doesn't lose the trailing buffer
 *   <li>IO errors are logged but never thrown — mod must not crash MC
 * </ul>
 */
public final class JsonlWriter {

    private static final Logger LOGGER = LoggerFactory.getLogger(JsonlWriter.class);

    private final Path outputPath;
    private boolean disabled = false;

    public JsonlWriter(Path outputPath) throws IOException {
        this.outputPath = outputPath;
        Files.createDirectories(outputPath.getParent());
        // Truncate any prior file from a previous launch — we want one
        // recording session per MC launch.
        Files.write(outputPath, new byte[0],
            StandardOpenOption.CREATE,
            StandardOpenOption.TRUNCATE_EXISTING);
        LOGGER.info("JsonlWriter initialised: {}", outputPath);
    }

    public void append(GameStateSample sample) {
        if (disabled) return;
        try {
            String line = sample.toJsonLine() + "\n";
            Files.write(outputPath, line.getBytes(StandardCharsets.UTF_8),
                StandardOpenOption.CREATE,
                StandardOpenOption.APPEND);
        } catch (IOException e) {
            LOGGER.warn("JsonlWriter append failed (disabling further writes): {}",
                e.getMessage());
            disabled = true;
        }
    }
}
