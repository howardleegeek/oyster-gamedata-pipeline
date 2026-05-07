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
        Path runDir = server.getRunDirectory().resolve("oyster_state");
        String safe = UNSAFE.matcher(playerName).replaceAll("_");
        if (safe.isEmpty()) {
            safe = "unknown";
        }
        return runDir.resolve(safe + ".jsonl");
    }
}
