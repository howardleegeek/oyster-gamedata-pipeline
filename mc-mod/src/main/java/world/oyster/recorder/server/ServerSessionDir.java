package world.oyster.recorder.server;

import net.minecraft.server.MinecraftServer;

import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.regex.Pattern;

/**
 * Resolves the per-player JSONL path for the server-side capture.
 *
 * <p>Howard 2026-05-07 (D16): cluster-side companion to {@link
 * world.oyster.recorder.SessionDir}. Difference:
 * <ul>
 *   <li>Output is server-relative (cluster runs ≥3 Paper instances
 *       independently — no shared filesystem assumption)
 *   <li>One file per connected player, keyed on username (not UUID
 *       because cluster's {@code buyer_spec_pipeline.sh} matches by
 *       {@code --bot-username})
 *   <li>{@code oyster_state/} subdir under server's run-dir keeps the
 *       output siloed from {@code world/}, {@code logs/}, {@code mods/}
 * </ul>
 *
 * <p>Username sanitisation: Minecraft allows usernames to contain
 * underscores and digits but NOT slashes or path separators (max 16
 * chars). We add a defensive regex strip just in case a future MC
 * version relaxes that rule.
 */
public final class ServerSessionDir {

    private static final Pattern UNSAFE = Pattern.compile("[^A-Za-z0-9_\\-]");

    private ServerSessionDir() {}

    public static Path outputPath(MinecraftServer server, String playerName) {
        // Howard 2026-05-07 (D19 cross-version fix): MinecraftServer
        // .getRunDirectory() returns java.io.File in MC 1.20.x but
        // java.nio.file.Path in 1.21.x. Use Object reflection so the same
        // bytecode works on either API. Loom's per-cell yarn mappings turn
        // the call into the right return type at compile time, but we need
        // a normalised Path for our oyster_state subdir lookup.
        Object runDirObj = server.getRunDirectory();
        Path runDir;
        if (runDirObj instanceof Path p) {
            runDir = p.resolve("oyster_state");
        } else if (runDirObj instanceof java.io.File f) {
            runDir = f.toPath().resolve("oyster_state");
        } else {
            // Defensive — should not happen; fall back to current dir.
            runDir = Paths.get(".").resolve("oyster_state");
        }
        String safe = UNSAFE.matcher(playerName).replaceAll("_");
        if (safe.isEmpty()) {
            safe = "unknown";
        }
        return runDir.resolve(safe + ".jsonl");
    }
}
