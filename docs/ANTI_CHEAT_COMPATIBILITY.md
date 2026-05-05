# Anti-Cheat Compatibility — 2026-05-05

> Will our recorder get flagged as cheating?
> **Short answer: no, for the games we ship today.**
> Long answer: depends on the anti-cheat tier — green/yellow/red below.

---

## Why our recorder is NOT a cheat by definition

What anti-cheat detects (and we **don't do any of it**):

1. ❌ Reading game memory (e.g. ESP wallhacks)
2. ❌ Injecting DLLs / hooking game functions
3. ❌ Manipulating network packets (e.g. teleport hacks)
4. ❌ Auto-aim / aimbot input synthesis tied to game state

What we actually do (all 100% passive, externally observable):

1. ✅ **OBS Studio screen capture** — same API every Twitch streamer uses
2. ✅ **Raw Input API** for keyboard/mouse logging — same API every macro program uses
3. ✅ **Mineflayer Java protocol client** — only on our own private Paper server, never against public servers

Technically: the recorder is **OBS + a passive input logger**. Every streamer on every platform runs equivalent software on these games 24/7 without bans.

---

## Traffic-light classification

### 🟢 GREEN — Always safe (single-player / our server / no anti-cheat)

| Game | Anti-cheat | Our path |
|---|---|---|
| **Minecraft Java (private Paper server)** | none | **production today** |
| Minecraft Java (single-player) | none | works |
| BeamNG.drive | none (single-player sim) | scaffolded (`docs/runbooks/BEAMNG_RUNBOOK.md`) |
| Factorio | none | scaffolded (`environments/factorio.py`) |
| Cities Skylines / Civilization VI / Stellaris | none | OBS-only |
| RimWorld / Kerbal Space Program | none | OBS-only |
| The Witcher 3 / RDR2 / Skyrim (offline) | none | OBS-only |
| Garry's Mod (single-player) | none | OBS-only |
| Roblox (with creator's permission) | passive | OBS-only |
| Forza Horizon 5 (single-player) | none | OBS-only |

**Recommended starting point** for new vendors. Zero compliance risk.

### 🟡 YELLOW — Multiplayer with passive / userland anti-cheat (OBS-recording is SAFE)

These games have anti-cheat (VAC, BattlEye, EAC) but specifically tolerate OBS / streaming. Streamers pour gameplay onto Twitch every day with VAC enabled.

| Game | Anti-cheat | Notes |
|---|---|---|
| **Counter-Strike 2** | VAC (Valve Anti-Cheat) | OBS = safe; **`demoparser2`** parses post-game `.dem` files (no live memory read) → 100% safe |
| Dota 2 | VAC | OBS = safe |
| Team Fortress 2 | VAC | OBS = safe |
| Fortnite | EAC + BattlEye | OBS = safe |
| Apex Legends | EAC | OBS = safe |
| Rust | EAC | OBS = safe |
| PUBG | BattlEye | OBS = safe |
| GTA V Online | BattlEye | OBS = safe |
| Hunt: Showdown | EAC | OBS = safe |
| Sea of Thieves | EAC | OBS = safe |
| WoW (retail) | Warden | passive OBS is safe; addons can flag |

**Vendor risk**: zero on the recording side. Our pipeline never auto-plays these games — vendors play normally with their own account; we record alongside.

### 🔴 RED — Kernel-level / paranoid anti-cheat (compatibility risk, NOT a ban risk)

These ship a **ring-0 kernel driver** that blocks some overlays / recorders for compatibility reasons (not because they think it's cheating). OBS sometimes works, sometimes shows a black screen, depending on game build + driver version.

| Game | Anti-cheat | What might happen |
|---|---|---|
| **Valorant** | Vanguard (Riot, kernel) | OBS may show black screen on game-capture mode; "monitor capture" works |
| League of Legends (recent) | Vanguard (rollout 2024+) | same as Valorant |
| Genshin Impact | mhyprot (kernel) | overlay-based recording sometimes blocked |
| Honkai: Star Rail | mhyprot | same |
| Wuthering Waves | similar kernel AC | same |
| Roblox (some experiences with hyperion) | hyperion | similar OBS edge cases |
| Older WoW with addon-detection | Warden | rare |

**Mitigation paths if a vendor wants to capture a RED game**:
1. **External capture card** — separate PC + Elgato capture card recording the HDMI out → bypasses ALL anti-cheat concerns (the game's machine has zero overlay; capture happens on a second machine)
2. **Monitor-capture mode in OBS** instead of game-capture-hook mode — usually works
3. **Buyer must explicitly opt-in** to RED-tier games and accept compatibility risk
4. **NEVER use the bot path** on RED games — only the recorder path (no Mineflayer-style bot exists for these games anyway)

---

## Bot path (Mineflayer) — anti-cheat scope

The Mineflayer bot is **only run against our own private Paper server**. It NEVER connects to:
- Hypixel
- Mineplex
- 2b2t
- any public Minecraft server

→ **Zero risk of bans** because we control the only server it touches. Server-side anti-cheat (NoCheatPlus, AAC, Matrix, etc.) is not installed on our Paper server.

If a buyer asks us to capture from a public MC server, the answer is **no — we use single-player or our own server**. That's a contract clause, not a technical limitation.

---

## What we tell each stakeholder

### Vendor onboarding
> "Our recorder is OBS Studio + a keyboard logger — same software every Twitch streamer uses. It does NOT read game memory, inject DLLs, or modify network traffic. You can run it alongside any single-player game or any multiplayer game with passive anti-cheat (VAC / EAC / BattlEye). For Valorant / Genshin / League with Vanguard, use a capture card setup we can recommend."

### Buyer
> "The pipeline is fully passive on the game's machine. For VAC/EAC/BattlEye titles (CS2, Fortnite, Apex, etc.) we capture exactly the way Twitch streamers do — no modifications. For Vanguard-class kernel anti-cheat we route through external capture-card setups. We never run automated bots against public servers."

### Investor
> "Our data-acquisition layer is anti-cheat-compatible by construction. We use the same Windows APIs (DirectShow + Raw Input) that the entire game-streaming industry uses. The only path that touches game internals is the Minecraft bot, which only runs on our own infrastructure. We have a documented red-tier mitigation (capture card) for kernel-level anti-cheat games."

---

## Game-by-game decision tree (for vendor support)

```
A vendor wants to record game X. Which tier?

  Single-player or our private server?
    → 🟢 GREEN. Just record.

  Multiplayer with VAC / BattlEye / EAC?
    → 🟡 YELLOW. Run OBS as usual. No bans.
       (CS2, Fortnite, Apex, Rust, PUBG, GTA Online, etc.)

  Multiplayer with Vanguard / mhyprot / hyperion / kernel AC?
    → 🔴 RED. Need capture card setup.
       (Valorant, LoL, Genshin, HSR, Wuthering Waves)

  Has the vendor disabled the anti-cheat?
    → STOP. Do not work with them.
       That actually IS cheating + violates the game's TOS.
```

---

## What we WILL NOT do (compliance red lines)

- ❌ Bypass any anti-cheat
- ❌ Read game memory
- ❌ Inject code into another process
- ❌ Distribute drivers / kernel modules
- ❌ Run automated bots against public servers
- ❌ Sell data captured in violation of a game's TOS

If a buyer asks for any of the above, we say no. That's a one-sentence policy — no exceptions.
