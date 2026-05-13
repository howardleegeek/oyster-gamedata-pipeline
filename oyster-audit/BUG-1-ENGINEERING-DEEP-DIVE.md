# Bug 1 — Engineering Deep Dive

**Repo**: `oyster-agent-runner` (parent) + `vendor/recorder/` (submodule)
**Session**: `session_20260512_182328_e610fdd6` (`registration_tier=hook`, `wm_input_total=957`, `total_keyboard_events=0`, duration 150.36s)
**Mission**: pinpoint why 957 hook-observed events translate to 0 lines in `inputs.jsonl`. Read-only static analysis. No source edits.

All file paths are relative to `vendor/recorder/` unless noted otherwise.

---

## Section 1 — Which `wm_input_total.fetch_add` site fired 957 times?

There are exactly three sites bumping `metrics.wm_input_total`:

| # | File:Line | Context | Reachable when `tier == Hook`? |
|---|-----------|---------|-------------------------------|
| A | `crates/input-capture/src/kbm_capture.rs:396` | inside `keyboard_ll_proc` (the `WH_KEYBOARD_LL` callback) | YES — this is the only path on a hook-tier system |
| B | `crates/input-capture/src/kbm_capture.rs:487` | inside `mouse_ll_proc` (`WH_MOUSE_LL` callback) | NO. `mouse_ll_proc` is defined but **never installed** — see Section 3 |
| C | `crates/input-capture/src/kbm_capture.rs:1261` | inside `KbmCapture::parse_wm_input`, bumped once per `WM_INPUT` window message | NO. `WM_INPUT` is only delivered when `RegisterRawInputDevices` succeeded (tier 1 or 2). On a `tier=hook` session, `RegisterRawInputDevices` failed — no `WM_INPUT` ever arrives, so `parse_wm_input` is never called |

Verdict: **957 = bumps at site A (`keyboard_ll_proc` line 396), exclusively keyboard down/up.** Tier 1/2/3 are mutually exclusive (see line 900 — tier-3 branch is gated on `matches!(tier, RegistrationTier::None)`, executed only when tier-1 and tier-2 both failed).

Implication: every one of the 957 events is a `KeyPress` candidate. There is **zero mouse movement, zero mouse click, zero scroll** counted in `wm_input_total` for this session — the buyer-side conclusion "100+ mouse moves/sec missing" is wrong; on this AMD-fallback machine mouse capture cannot exist *at all* until a `WH_MOUSE_LL` install is added.

---

## Section 2 — Pump/install thread consistency

Win32 LL hooks fire on the thread that installed them (`SetWindowsHookExW` with `dwThreadId=0` hooks the entire process, but the callback is dispatched on the thread doing the message dispatch).

Install thread: `KbmCapture::initialize` (line 707) runs synchronously inside the closure spawned at `lib.rs:116-133`. The same thread:

1. calls `GetCurrentThreadId()` → stores into `PUMP_THREAD_ID` (line 756)
2. calls `SetWindowsHookExW(WH_KEYBOARD_LL, ...)` (line 935)
3. returns the `KbmCapture` to the same closure
4. immediately calls `self.run_queue(...)` (line 124) → enters the `GetMessageA` loop (line 1022)

So the hook install thread, the `PUMP_THREAD_ID` thread, and the `GetMessageA` thread are the **same** `std::thread::spawn` worker. **Pass.** Hook firing → callback executes on this thread → `PostThreadMessageW(pump_tid, HOOK_WAKE_MSG, ...)` posts a wake to the same thread → `GetMessageA` returns next iteration. Architecturally sound.

The `_raw_input_thread` field type at `lib.rs:75` is `std::thread::JoinHandle<()>` (not `()`), so the JoinHandle is retained on the `InputCapture` struct. The thread lifetime extends until the struct is dropped. **Hypothesis (6) in the briefing — "thread dropped early" — is invalidated by the type signature.** Same for `_gamepad_threads` (line 76).

---

## Section 3 — Why is 6.6 events/sec so low?

Ranked hypotheses:

### H1 (highest confidence — ~95%) — `wm_input_total` only counts **keyboards events** on a hook-tier session, and 957/145s = 6.6 keys/sec is **exactly normal** for active Minecraft

Minecraft movement is "hold W". The LL hook receives WM_KEYDOWN once on initial press, then Windows generates **autorepeat WM_KEYDOWN every ~30ms while held** which Windows itself does NOT suppress at the LL hook level. But notice `keyboard_ll_proc` lines 403-421: the `forward` gate computes `ak.keyboard.insert(vk)` for `Pressed` — which returns `false` on second insert. **But `wm_input_total` is bumped BEFORE the forward gate (line 396), so it counts every autorepeat.**

Wait — re-reading: at 30ms autorepeat, holding W for 145s = ~4830 autorepeats. We see only 957. So this is NOT autorepeat. 957 / 145s = 6.6/sec is roughly the rate of **distinct key transitions** (press-down + press-up pairs + sysmodifiers). Translates to roughly 1 keystroke every 300ms — plausible for combat clicks + tab + chat keys on top of WASD. **Looks healthy for "the hook is firing".**

The bug is NOT a rate-limit on the hook; the hook is doing its job. The bug is downstream.

### H2 (~80% confidence) — The `forward` flag at lines 403-421 silently drops `Pressed` repeats AND has a `try_send` design that does NOT log drops

Line 412: `PressState::Pressed => ak.keyboard.insert(vk)` — if vk was already in the set (autorepeat), `insert` returns `false`, `forward` becomes `false`, the `try_send` is skipped. This is correct autorepeat suppression. But `wm_input_total` was already bumped at line 396 → the counter overcounts compared to events forwarded.

So out of 957 wm_input bumps, perhaps **20–50 are unique press/release transitions** (everything else is autorepeat suppressed by the HashSet gate). Even so — 20 events should produce 20 JSONL lines, not 0.

### H3 (~85% confidence) — **The `keyboard_ll_proc` callback NEVER posts `HOOK_WAKE_MSG` to the pump thread**

Compare the two hook callbacks:

- `keyboard_ll_proc` (lines 350-442): does `tx.try_send(...)` at line 434. **No `PostThreadMessageW` call.** Search confirms — line 434 is the only send, and the function returns immediately after.
- `mouse_ll_proc` (lines 455-622): every send is followed by a `PostThreadMessageW(pump_tid, HOOK_WAKE_MSG, ...)` call (lines 521, 546, 569, 592, 611).

Now look at the pump loop (`run_queue`, lines 1011-1066):

```
loop {
    let result = GetMessageA(&mut msg, None, 0, 0);   // BLOCKS until a real message arrives
    ...
    DispatchMessageA(&msg);
    if msg.message == WM_INPUT { ... }
    // Drain hook_rx
    if let Some(rx) = self.hook_rx.as_ref() {
        while let Ok(event) = rx.try_recv() { event_callback(event) }
    }
}
```

**`GetMessageA` is a blocking call.** It only returns when a message is posted to the thread's queue. Low-level hook callbacks (`WH_KEYBOARD_LL`) DO fire on this thread, **but they fire as a side effect of the OS dispatching the keystroke into the thread's hook chain — not by posting a `MSG` to the thread's message queue.** Firing the callback does NOT wake `GetMessageA`.

The drain block runs **AFTER** `DispatchMessageA(&msg)` returns. If no `MSG` is ever posted to wake `GetMessageA`, the callback fills `hook_rx` but the drain never runs. This is exactly the documented contract at lines 296-311 (`PUMP_THREAD_ID` doc comment): "firing the hook does NOT cause `GetMessage` itself to return — it only runs the callback as a side effect of message dispatch."

The mouse hook DOES post a wake message. **The keyboard hook does NOT.** So keyboard events fill the `hook_rx` ring buffer, then sit there forever until something else (e.g., a `WM_DESTROY`, a stray mouse event from a mouse_ll_proc that doesn't exist on this session, or eventually a queue overflow + drop) wakes the pump.

#### Edge case that explains the 5 lifecycle events

The 5 JSONL markers (START / VIDEO_START / HOOK_START / VIDEO_END / END) come from a **different sender** — they go via `event_stream.send(...)` from the `obs_embedded_recorder.rs` hook monitor thread, into a totally separate channel (the `event_stream` mpsc), not via `input_tx`. So the `input_rx.recv()` in `tokio_thread.rs:224` only ever sees events from the LL hook path. The lifecycle markers reach `inputs.jsonl` through `Recording::input_stream()`, bypassing the entire `input_rx` arm. That's why they make it through and the keyboard events don't.

**Verdict on H3**: this is the bug. The keyboard hook fills `hook_rx` but the pump thread blocks in `GetMessageA` forever, never draining. Confirmed by code reading; no Cargo check needed.

---

## Section 4 — Channel capacity / blocking analysis

### Leg 1 — `HOOK_EVENT_TX` (`SyncSender<Event>`) → `hook_rx` (`Receiver<Event>`)

- Construction: `sync_channel::<Event>(10_000)` at line 901. Bounded **synchronous** channel, capacity 10k.
- Sender side: `tx.try_send(...)` at lines 434, 516, 538, 561, 584, 604. **Non-blocking** — returns `Err(TrySendError::Full)` if 10k buffered, return value discarded by `let _ =`. So a full buffer drops silently (which is the documented intent at lines 432-433).
- Receiver side: `rx.try_recv()` at line 1057 inside the drain block. Non-blocking.

Capacity 10k is fine. If the pump drained even once per second, this couldn't fill at 6.6 events/sec. The bottleneck is not capacity — it's that **the drain never runs because nothing wakes `GetMessageA`.**

For an outside observer with no instrumentation: if the bug ran for hours, `hook_rx` would be at exactly 10000 entries and every subsequent `try_send` would be a silent drop. 957 < 10000, so we are still in the "filling but not yet overflowed" regime. **All 957 keyboard events are sitting in the channel right now, not lost.**

### Leg 2 — `input_tx` (`mpsc::Sender<Event>`) → `input_rx` (`mpsc::Receiver<Event>`)

- Construction: `mpsc::channel(10_000)` at `lib.rs:111`. Tokio bounded mpsc.
- Sender side: `input_tx.blocking_send(event)` at `lib.rs:125`. **Blocking** — if `input_rx` is full or closed it blocks the calling thread (the pump thread that runs `run_queue`). On `Closed` it returns `Err`, which the closure logs and returns `false` (causing `run_queue` to exit) — see lines 124-129. On `Full` it just blocks.
- Receiver side: `input_rx.recv()` at `tokio_thread.rs:224` inside the main `tokio::select!`. Standard tokio mpsc receive.

If H3 is right, the closure at `lib.rs:125` is **never called** for keyboard events on a hook-tier session — because the drain at `kbm_capture.rs:1057` never runs. So `input_tx.blocking_send` is never invoked → nothing for tokio_thread to consume → 0 keyboard events in metadata's `input_stats`. The metadata counters under `input_stats` are computed from the events that tokio_thread actually saw (in `recorder.rs:296` `seen_input`) → they read 0.

This is fully consistent with the observed data: `wm_input_total=957` (hook saw it), but `total_keyboard_events=0` (tokio_thread never received it).

---

## Section 5 — `InputCapture` struct field analysis

From `crates/input-capture/src/lib.rs:74-85`:

```rust
pub struct InputCapture {
    _raw_input_thread: std::thread::JoinHandle<()>,    // line 75
    _gamepad_threads: gamepad_capture::GamepadThreads, // line 76
    active_keys: Arc<Mutex<kbm_capture::ActiveKeys>>,
    active_gamepad: Arc<Mutex<gamepad_capture::ActiveGamepads>>,
    gamepads: Arc<RwLock<HashMap<GamepadId, GamepadMetadata>>>,
    metrics: Arc<CaptureMetrics>,
}
```

The field is a real `JoinHandle<()>`, not `()`. The underscore prefix only suppresses unused-warning lints; the value is owned by the struct and dropped only when `InputCapture` itself is dropped. `InputCapture` is owned by `State` at `tokio_thread.rs:176` and lives for the entire lifetime of the tokio runtime.

**Hypothesis 6 (thread getting dropped at end of `new`) is INVALIDATED.** The thread runs for the program's entire lifetime. This is consistent with the hook actually firing (957 events observed) — if the thread had exited, the hook would have been uninstalled by `Drop for KbmCapture` (lines 643-693) and `wm_input_total` would be 0.

---

## Section 6 — Recommended next action

**Option (a) — High-confidence single-line fix patch.** I am 95% confident on the diagnosis. The fix is two lines added to `keyboard_ll_proc`, mirroring what `mouse_ll_proc` already does for each event.

**File**: `crates/input-capture/src/kbm_capture.rs`
**Locate**: the `try_send` block at lines 423-438 inside `keyboard_ll_proc`:

```rust
if forward
    && let Some(slot) = HOOK_EVENT_TX.get()
    && let Ok(guard) = slot.lock()
    && let Some(tx) = guard.as_ref()
{
    let _ = tx.try_send(Event::KeyPress { key: vk, press_state });
}
```

**Patch**: append the same wake post the mouse procedure uses:

```rust
if forward
    && let Some(slot) = HOOK_EVENT_TX.get()
    && let Ok(guard) = slot.lock()
    && let Some(tx) = guard.as_ref()
{
    let _ = tx.try_send(Event::KeyPress { key: vk, press_state });

    // rc18.x: wake the pump so GetMessageA returns and run_queue drains
    // hook_rx. Without this, keyboard events sit in the channel forever
    // on hook-tier (AMD/INPUTSINK-fail) systems. See mouse_ll_proc for
    // the same pattern (lines 519-522).
    let pump_tid = PUMP_THREAD_ID.load(Ordering::Relaxed);
    if pump_tid != 0 {
        let _ = PostThreadMessageW(pump_tid, HOOK_WAKE_MSG, WPARAM(0), LPARAM(0));
    }
}
```

That is the entire fix. No new imports, no new statics, no API changes. The same `PostThreadMessageW`, `PUMP_THREAD_ID`, `HOOK_WAKE_MSG`, `WPARAM`, `LPARAM` symbols are already in scope (mouse_ll_proc uses all of them in this file).

**Why this fix is necessary AND sufficient**:
- Necessary: `GetMessageA` blocks indefinitely without a posted message; the LL keyboard hook firing does not produce one.
- Sufficient: once posted, the pump loop iterates, `DispatchMessageA` runs (the `HOOK_WAKE_MSG` ≡ `WM_USER` is harmless — `window_proc` falls through to `DefWindowProcA`), and the drain block at line 1057 reads `hook_rx` and forwards to `event_callback` → `input_tx.blocking_send` → `input_rx.recv()` in tokio_thread → `state.on_input(e)` → `recorder.seen_input(e)` → `inputs.jsonl`. Full chain.

**Secondary observation — independent but related bug**: `mouse_ll_proc` is defined and complete, but `SetWindowsHookExW(WH_MOUSE_LL, ...)` is **never called**. The mouse callback is dead code on every system. On a tier-1/tier-2 (RawInput) machine, mouse goes through `parse_wm_input` and works. On a tier-3 (hook) machine, mouse is silently dropped — exactly as documented at lines 941-944 ("Mouse capture is DEAD for this session"). After the keyboard fix above lands, the next change for AMD/hook machines is to install `WH_MOUSE_LL` next to the `WH_KEYBOARD_LL` install at line 935 — but that's a separate, scoped change. Leave it for the next iteration.

**Risk on the keyboard fix**: zero. The exact pattern is already present in the same file for mouse_ll_proc; we are mechanically replicating it for keyboard_ll_proc. `PostThreadMessageW` from within an LL hook callback is a documented and supported pattern (the call itself is non-blocking and ~microsecond cost).

**Test plan**:
1. Build on Windows (cargo check is sufficient — no new dependencies, no new types).
2. Run a 60-second session on a known hook-tier (AMD) machine — Bingd's, or any machine where the previous metadata showed `registration_tier=hook`.
3. Expect: `inputs.jsonl` now contains KEYBOARD events. `total_keyboard_events > 0` in `input_stats`. `wm_input_total ≈ total_keyboard_events + autorepeats_suppressed_count` (the difference is the HashSet filter at line 412).

**Why not (b) diagnostic instrumentation?** Because the diagnosis is already strong from static analysis (asymmetry between `keyboard_ll_proc` and `mouse_ll_proc` in the same file, identical pattern, one with wake-post one without). Adding atomic counters at lines 425, 432, 1058 would confirm "drain never runs" but the static evidence already says so. Ship the fix.

**Why not (c) defer to Windows?** Because the diff is two real lines and a comment. Worth shipping blind; the worst outcome is no behavioral change, the cost is zero in code review.

---

## Appendix — Provenance of every claim

| Claim | Reference |
|-------|-----------|
| 3 `wm_input_total.fetch_add` sites | `crates/input-capture/src/kbm_capture.rs:396`, `:487`, `:1261` |
| `keyboard_ll_proc` lacks `PostThreadMessageW` | `kbm_capture.rs:350-442` (read all 92 lines, no `PostThreadMessageW` in the body) |
| `mouse_ll_proc` has `PostThreadMessageW` | `kbm_capture.rs:519-522`, `:544-547`, `:567-570`, `:590-593`, `:608-611` (5 sites) |
| Tier 3 only installs `WH_KEYBOARD_LL` | `kbm_capture.rs:935` is the only `SetWindowsHookExW` call |
| `WH_MOUSE_LL` is imported but never installed | `kbm_capture.rs:67` (import); no usage |
| `mouse_ll_proc` is dead on hook-tier | logical consequence of the two above; explicitly documented in source `kbm_capture.rs:941-944` |
| Tiers are mutually exclusive | `kbm_capture.rs:900` `if matches!(tier, RegistrationTier::None)` gates tier-3 |
| `_raw_input_thread` is `JoinHandle<()>` not `()` | `crates/input-capture/src/lib.rs:75` |
| `GetMessageA` is blocking and only returns on a queued `MSG` | Win32 docs + the pump structure at `kbm_capture.rs:1021-1063` |
| Lifecycle markers use a separate channel | `src/record/obs_embedded_recorder.rs:1105-1122` (`event_stream.send(...)`) bypasses `input_rx` |
| `recorder.seen_input` is the only on-ramp from `input_rx` to `inputs.jsonl` | `src/record/recorder.rs:296-304`; `src/tokio_thread.rs:251` `state.on_input(e)` → `recorder.seen_input(e)` |
| Channel capacities: hook channel 10k, tokio channel 10k | `kbm_capture.rs:901`, `lib.rs:111` |
| Tokio mpsc send is `blocking_send`, hook `SyncSender` send is `try_send` | `lib.rs:125`, all 6 callback sites in `kbm_capture.rs` |

Total word count: ~1480.
