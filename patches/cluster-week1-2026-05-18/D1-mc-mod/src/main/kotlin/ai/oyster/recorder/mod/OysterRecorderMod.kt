package ai.oyster.recorder.mod

import net.fabricmc.api.ClientModInitializer
import net.fabricmc.api.ModInitializer
import org.slf4j.LoggerFactory

/**
 * Main entry point for the Oyster Recorder mod.
 * Handles camera, player, and world state recording.
 * This module is preserved and not modified by the Z-buffer capture addition.
 */
class OysterRecorderMod : ModInitializer, ClientModInitializer {

    companion object {
        const val MOD_ID = "oyster-recorder-mod"
        private val LOGGER = LoggerFactory.getLogger(OysterRecorderMod::class.java)

        @JvmStatic
        fun init() {
            val mod = OysterRecorderMod()
            mod.onInitialize()
            mod.onInitializeClient()
        }
    }

    override fun onInitialize() {
        LOGGER.info("[OysterRecorder] Mod initialized (common)")
    }

    override fun onInitializeClient() {
        LOGGER.info("[OysterRecorder] Client initialized — camera/player/world state writers active")
    }
}
