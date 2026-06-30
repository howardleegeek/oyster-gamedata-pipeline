"""Phase 1 deterministic replay tool.

Buyers receive a Phase 1 trajectory bundle (manifest.json + cot.jsonl +
metadata.jsonl + inputs.jsonl) and need to be able to:

  1. Walk the agent's decisions step-by-step (`Replayer.iter_steps`).
  2. Verify the bundle is internally consistent — same step indices
     across streams, monotonic timestamps, no orphan events, manifest
     totals match what the streams contain (`Replayer.verify_consistency`).
  3. Re-execute the recorded actions against a fresh environment and
     detect divergence between the recorded observations and the
     re-executed ones (`Replayer.replay_against`).

This module is read-only with respect to the bundle on disk — it never
modifies the input files. Output is always returned as Python objects;
the CLI in `cli.py` is the only place that prints to stdout.

Design notes
------------
* Streams are treated as the canonical source of truth, NOT
  ``trajectory.jsonl``. The bundle a buyer receives is the four-file
  layout, so we never depend on the upstream mixed log existing.
* "Step" reconstruction: the four streams encode step boundaries
  redundantly. AGENT_STEP carries an explicit ``event_args.step``;
  LLM_THINKING also carries it. ACTION/OBSERVATION/LLM_REASONING/REWARD
  events do NOT carry step indices — they share the AGENT_STEP timestamp
  for that step. We index by AGENT_STEP timestamps in metadata.jsonl,
  then attach co-timestamped events from cot/inputs.
* Live Minecraft replay is Phase 2 — for Phase 1 we replay against
  ``MockEnvironment`` (or any other ``Environment`` the caller passes
  in). Divergence detection is structural: we compare action-by-action
  and surface mismatches by step.
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from oyster_agent_runner.environments.base import Environment, MockEnvironment
from oyster_agent_runner.minecraft_streams import (
    COT_FILENAME,
    INPUTS_FILENAME,
    METADATA_FILENAME,
)
from oyster_agent_runner.schema import (
    EVENT_ACTION,
    EVENT_AGENT_STEP,
    EVENT_END,
    EVENT_LLM_REASONING,
    EVENT_LLM_THINKING,
    EVENT_OBSERVATION,
    EVENT_REWARD,
    EVENT_START,
)

# A small tolerance for floating point timestamp comparisons. The
# producer writes monotonic offsets, so equal-timestamp events are
# byte-identical floats — but we still match within a tiny epsilon to
# survive any future jitter introduced by Phase 2 video alignment.
_TIMESTAMP_EPSILON_SEC = 1e-9


# --- Data classes -----------------------------------------------------------


@dataclass(frozen=True)
class ReplayStep:
    """One reconstructed step of the agent's interaction.

    Fields are populated best-effort: for example, a malformed bundle
    that's missing the OBSERVATION line for step 3 will still yield a
    ``ReplayStep`` with ``observation=None``. Use
    ``Replayer.verify_consistency`` to detect such gaps explicitly.
    """

    step_idx: int
    observation: Any | None
    thinking: str | None
    reasoning: str | None
    action: dict[str, Any] | None
    reward: float | None
    success_flag: bool
    timing_ms: float
    timestamp_sec: float


@dataclass
class ConsistencyReport:
    """Summary of a structural validation pass on the bundle."""

    ok: bool
    issues: list[str] = field(default_factory=list)
    cot_event_count: int = 0
    metadata_event_count: int = 0
    input_event_count: int = 0
    max_timestamp_sec: float = 0.0
    step_count: int = 0
    manifest_matches_streams: bool = True

    def add(self, issue: str) -> None:
        """Add a consistency issue to the report.

        Args:
            issue: Description of the consistency problem found.

        Returns:
            None. Modifies this object in-place.
        """
        self.issues.append(issue)
        self.ok = False


@dataclass
class ReplayDriftReport:
    """Result of re-executing recorded actions against a fresh env."""

    steps_executed: int
    steps_diverged: list[int] = field(default_factory=list)
    divergence_reasons: dict[int, str] = field(default_factory=dict)
    early_termination_step: int | None = None
    error_message: str | None = None

    @property
    def ok(self) -> bool:
        """Check if the replay was successful.

        Returns:
            True if no steps diverged and no error occurred, False otherwise.
        """
        return not self.steps_diverged and self.error_message is None


# --- Replayer ---------------------------------------------------------------


class Replayer:
    """Walk a Phase 1 bundle and reconstruct / verify / replay steps.

    Parameters
    ----------
    manifest_path:
        Path to ``manifest.json`` inside the bundle directory. The other
        three streams are resolved relative to the manifest's directory
        using the names recorded in ``manifest['outputs']``.
    """

    def __init__(self, manifest_path: Path) -> None:
        self.manifest_path = Path(manifest_path)
        if not self.manifest_path.exists():
            raise FileNotFoundError(f"manifest not found: {self.manifest_path}")
        try:
            self.manifest: dict[str, Any] = json.loads(
                self.manifest_path.read_text(encoding="utf-8")
            )
        except json.JSONDecodeError as exc:
            raise ValueError(f"manifest is not valid JSON: {exc}") from exc
        if not isinstance(self.manifest, dict):
            raise ValueError("manifest must be a JSON object")

        self.bundle_dir = self.manifest_path.parent
        outputs = self.manifest.get("outputs") or {}
        self.cot_path = self.bundle_dir / (outputs.get("cot") or COT_FILENAME)
        self.metadata_path = self.bundle_dir / (outputs.get("metadata") or METADATA_FILENAME)
        self.inputs_path = self.bundle_dir / (outputs.get("inputs") or INPUTS_FILENAME)

        # Streams are loaded lazily on first access.
        self._cot_events: list[dict[str, Any]] | None = None
        self._metadata_events: list[dict[str, Any]] | None = None
        self._input_events: list[dict[str, Any]] | None = None

    # --- Public API ----------------------------------------------------------

    def iter_steps(self) -> list[ReplayStep]:
        """Reconstruct ordered ``ReplayStep`` records from the streams.

        Returns a list (not a generator) so callers can index into the
        result and take ``len(...)``. For very large bundles we may swap
        in a streaming implementation in Phase 2.
        """
        cot = self._load_cot()
        metadata = self._load_metadata()

        # Index AGENT_STEP events by their explicit step index — this is the
        # spine the rest of the events are attached to.
        agent_steps_by_idx: dict[int, dict[str, Any]] = {}
        for ev in metadata:
            if ev.get("event_type") == EVENT_AGENT_STEP:
                args = ev.get("event_args") or {}
                idx = args.get("step")
                if isinstance(idx, int):
                    # First occurrence wins — duplicates are reported by
                    # verify_consistency, not silently overwritten.
                    agent_steps_by_idx.setdefault(idx, ev)

        # Bucket co-timestamped events from each stream around each
        # AGENT_STEP. We use a small epsilon to tolerate float drift.
        steps: list[ReplayStep] = []
        for idx in sorted(agent_steps_by_idx.keys()):
            anchor = agent_steps_by_idx[idx]
            anchor_ts = float(anchor.get("timestamp", 0.0))
            success_flag = bool((anchor.get("event_args") or {}).get("success", False))

            observation = self._find_event_args_at(metadata, anchor_ts, EVENT_OBSERVATION)
            reward_args = self._find_event_args_at(metadata, anchor_ts, EVENT_REWARD)
            reasoning_args = self._find_event_args_at(cot, anchor_ts, EVENT_LLM_REASONING)
            action_args = self._find_event_args_at(cot, anchor_ts, EVENT_ACTION)
            # LLM_THINKING is matched on its own ``step`` field, not
            # timestamp — the runner emits it with its own timestamp
            # slightly before the rest of the step events.
            thinking_args = self._find_thinking_for_step(cot, idx)

            obs_value = None
            if isinstance(observation, dict) and "value" in observation:
                obs_value = observation["value"]
            elif observation is not None:
                obs_value = observation

            reward_val: float | None = None
            if isinstance(reward_args, dict):
                raw = reward_args.get("value")
                if isinstance(raw, (int, float)):
                    reward_val = float(raw)

            reasoning_text: str | None = None
            if isinstance(reasoning_args, dict):
                txt = reasoning_args.get("text")
                if isinstance(txt, str):
                    reasoning_text = txt

            thinking_text: str | None = None
            if isinstance(thinking_args, dict):
                txt = thinking_args.get("text")
                if isinstance(txt, str):
                    thinking_text = txt

            action_dict: dict[str, Any] | None = None
            if isinstance(action_args, dict):
                action_dict = action_args

            # Timing: gap between this step and the previous one (for the
            # first step, since-START). Reported in milliseconds.
            if steps:
                timing_ms = max(0.0, (anchor_ts - steps[-1].timestamp_sec) * 1000.0)
            else:
                start_ts = self._stream_start_timestamp(metadata)
                timing_ms = max(0.0, (anchor_ts - start_ts) * 1000.0)

            steps.append(
                ReplayStep(
                    step_idx=idx,
                    observation=obs_value,
                    thinking=thinking_text,
                    reasoning=reasoning_text,
                    action=action_dict,
                    reward=reward_val,
                    success_flag=success_flag,
                    timing_ms=timing_ms,
                    timestamp_sec=anchor_ts,
                )
            )
        return steps

    def verify_consistency(self) -> ConsistencyReport:
        """Cross-check timestamps + step alignment + manifest totals.

        Checks performed:
          * Each stream's timestamps are non-decreasing.
          * Across the metadata stream, AGENT_STEP indices are
            contiguous starting at 0 (no missing steps).
          * Every ACTION in cot.jsonl has a co-timestamped ACTION in
            inputs.jsonl, and vice versa.
          * Every step has at least one OBSERVATION + ACTION.
          * No orphan events: events outside [START_ts, END_ts].
          * Manifest counts match the actual stream sizes.
          * Manifest result.total_steps matches the number of
            AGENT_STEP events in metadata.
          * Manifest max_timestamp_sec matches the maximum observed
            timestamp.
        """
        cot = self._load_cot()
        metadata = self._load_metadata()
        inputs = self._load_inputs()

        report = ConsistencyReport(
            ok=True,
            cot_event_count=len(cot),
            metadata_event_count=len(metadata),
            input_event_count=len(inputs),
        )

        # Per-stream monotonicity.
        for stream_name, events in (
            ("cot.jsonl", cot),
            ("metadata.jsonl", metadata),
            ("inputs.jsonl", inputs),
        ):
            last_ts: float | None = None
            for i, ev in enumerate(events):
                ts = ev.get("timestamp")
                if not isinstance(ts, (int, float)):
                    report.add(f"{stream_name}: line {i + 1} has non-numeric timestamp {ts!r}")
                    continue
                if last_ts is not None and ts < last_ts - _TIMESTAMP_EPSILON_SEC:
                    report.add(
                        f"{stream_name}: timestamp regressed at line {i + 1} "
                        f"({last_ts} -> {ts})"
                    )
                last_ts = ts

        # max timestamp across all streams.
        all_ts: list[float] = []
        for events in (cot, metadata, inputs):
            all_ts.extend(
                float(ev["timestamp"])
                for ev in events
                if isinstance(ev.get("timestamp"), (int, float))
            )
        if all_ts:
            report.max_timestamp_sec = max(all_ts)

        # Step indices in metadata: contiguous from 0?
        agent_step_indices: list[int] = []
        for ev in metadata:
            if ev.get("event_type") == EVENT_AGENT_STEP:
                idx = (ev.get("event_args") or {}).get("step")
                if isinstance(idx, int):
                    agent_step_indices.append(idx)
        report.step_count = len(agent_step_indices)
        if agent_step_indices:
            expected = list(range(min(agent_step_indices), max(agent_step_indices) + 1))
            if agent_step_indices != expected:
                report.add(
                    f"metadata.jsonl: AGENT_STEP indices not contiguous "
                    f"(got {agent_step_indices}, expected {expected})"
                )

        # Cross-stream ACTION alignment: every cot ACTION should appear in
        # inputs at the same timestamp, and vice versa.
        cot_action_ts = sorted(
            float(ev["timestamp"])
            for ev in cot
            if ev.get("event_type") == EVENT_ACTION
            and isinstance(ev.get("timestamp"), (int, float))
        )
        input_action_ts = sorted(
            float(ev["timestamp"])
            for ev in inputs
            if ev.get("event_type") == EVENT_ACTION
            and isinstance(ev.get("timestamp"), (int, float))
        )
        if len(cot_action_ts) != len(input_action_ts):
            report.add(
                f"ACTION count mismatch: cot={len(cot_action_ts)} " f"inputs={len(input_action_ts)}"
            )
        else:
            for c_ts, i_ts in zip(cot_action_ts, input_action_ts, strict=False):
                if abs(c_ts - i_ts) > _TIMESTAMP_EPSILON_SEC:
                    report.add(f"ACTION timestamp drift between cot ({c_ts}) and inputs ({i_ts})")

        # Per-step required-event check.
        for idx in agent_step_indices:
            anchor_ts = next(
                (
                    float(ev["timestamp"])
                    for ev in metadata
                    if ev.get("event_type") == EVENT_AGENT_STEP
                    and (ev.get("event_args") or {}).get("step") == idx
                    and isinstance(ev.get("timestamp"), (int, float))
                ),
                None,
            )
            if anchor_ts is None:
                continue
            if self._find_event_args_at(metadata, anchor_ts, EVENT_OBSERVATION) is None:
                report.add(f"step {idx}: missing OBSERVATION in metadata.jsonl")
            if self._find_event_args_at(cot, anchor_ts, EVENT_ACTION) is None:
                report.add(f"step {idx}: missing ACTION in cot.jsonl")

        # Orphan event check: every event must lie inside [START_ts, END_ts]
        # of its own stream, if both markers exist.
        for stream_name, events in (
            ("cot.jsonl", cot),
            ("metadata.jsonl", metadata),
            ("inputs.jsonl", inputs),
        ):
            start_ts = self._stream_event_timestamp(events, EVENT_START)
            end_ts = self._stream_event_timestamp(events, EVENT_END)
            if start_ts is None or end_ts is None:
                continue
            for ev in events:
                ts = ev.get("timestamp")
                if not isinstance(ts, (int, float)):
                    continue
                if ts < start_ts - _TIMESTAMP_EPSILON_SEC:
                    report.add(
                        f"{stream_name}: orphan event "
                        f"{ev.get('event_type')} at {ts} (before START {start_ts})"
                    )
                if ts > end_ts + _TIMESTAMP_EPSILON_SEC:
                    report.add(
                        f"{stream_name}: orphan event "
                        f"{ev.get('event_type')} at {ts} (after END {end_ts})"
                    )

        # Manifest sanity.
        alignment = self.manifest.get("alignment") or {}
        manifest_cot = alignment.get("cot_event_count")
        manifest_md = alignment.get("metadata_event_count")
        manifest_in = alignment.get("input_event_count")
        manifest_max_ts = alignment.get("max_timestamp_sec")
        result = self.manifest.get("result") or {}
        manifest_total_steps = result.get("total_steps")

        if manifest_cot is not None and manifest_cot != len(cot):
            report.add(
                f"manifest.alignment.cot_event_count={manifest_cot} "
                f"but cot.jsonl has {len(cot)} lines"
            )
            report.manifest_matches_streams = False
        if manifest_md is not None and manifest_md != len(metadata):
            report.add(
                f"manifest.alignment.metadata_event_count={manifest_md} "
                f"but metadata.jsonl has {len(metadata)} lines"
            )
            report.manifest_matches_streams = False
        if manifest_in is not None and manifest_in != len(inputs):
            report.add(
                f"manifest.alignment.input_event_count={manifest_in} "
                f"but inputs.jsonl has {len(inputs)} lines"
            )
            report.manifest_matches_streams = False
        if (
            isinstance(manifest_max_ts, (int, float))
            and report.max_timestamp_sec
            and abs(manifest_max_ts - report.max_timestamp_sec) > _TIMESTAMP_EPSILON_SEC
        ):
            report.add(
                f"manifest.alignment.max_timestamp_sec={manifest_max_ts} "
                f"but observed max is {report.max_timestamp_sec}"
            )
            report.manifest_matches_streams = False
        if isinstance(manifest_total_steps, int) and manifest_total_steps != len(
            agent_step_indices
        ):
            report.add(
                f"manifest.result.total_steps={manifest_total_steps} "
                f"but metadata.jsonl has {len(agent_step_indices)} AGENT_STEP events"
            )
            report.manifest_matches_streams = False

        # Final observation cross-check: the last AGENT_STEP's success flag
        # should align with manifest.result.success.
        manifest_success = result.get("success")
        if isinstance(manifest_success, bool) and agent_step_indices:
            last_step_idx = max(agent_step_indices)
            last_anchor = next(
                (
                    ev
                    for ev in metadata
                    if ev.get("event_type") == EVENT_AGENT_STEP
                    and (ev.get("event_args") or {}).get("step") == last_step_idx
                ),
                None,
            )
            if last_anchor is not None:
                last_success = bool((last_anchor.get("event_args") or {}).get("success", False))
                if last_success != manifest_success:
                    report.add(
                        f"manifest.result.success={manifest_success} but final "
                        f"AGENT_STEP.success={last_success}"
                    )

        return report

    def replay_against(
        self,
        env: Environment | None = None,
        *,
        seed: int | None = None,
    ) -> ReplayDriftReport:
        """Re-execute the recorded actions against a fresh ``Environment``.

        Parameters
        ----------
        env:
            The environment to replay against. Defaults to a fresh
            ``MockEnvironment`` whose ``done_after_steps`` matches the
            recorded step count, so a clean recording roundtrips with
            zero divergence.
        seed:
            Seed passed to ``env.reset(...)``. Defaults to the recorded
            seed if present in the first observation, else 0.

        Divergence detection
        --------------------
        For each recorded step we re-issue the recorded action and
        compare:
          * The observation returned by ``env.step`` vs the recorded
            ``OBSERVATION`` event.
          * The reward returned vs the recorded REWARD.
          * The ``done`` flag vs the recorded ``success`` flag at the
            final step.
        Any mismatch is reported as a divergence reason on that step.
        """
        steps = self.iter_steps()
        if not steps:
            return ReplayDriftReport(steps_executed=0)

        if env is None:
            env = MockEnvironment(done_after_steps=len(steps))

        report = ReplayDriftReport(steps_executed=0)
        if seed is None:
            first_obs = steps[0].observation
            if isinstance(first_obs, dict) and isinstance(first_obs.get("seed"), int):
                seed = int(first_obs["seed"])
            else:
                seed = 0

        try:
            initial_obs = env.reset(seed=seed)
        except Exception as exc:  # noqa: BLE001 — surface env failures to caller
            report.error_message = f"env.reset failed: {type(exc).__name__}: {exc}"
            return report

        # Optional: if the recorded first observation matches what reset()
        # returned, great; if not, log a step-0 divergence but keep going.
        recorded_first = steps[0].observation
        if recorded_first is not None and not _shallow_equal(initial_obs, recorded_first):
            report.steps_diverged.append(steps[0].step_idx)
            report.divergence_reasons[steps[0].step_idx] = (
                f"reset observation diverged: env={initial_obs!r} recorded={recorded_first!r}"
            )

        for s in steps:
            if s.action is None:
                report.steps_diverged.append(s.step_idx)
                report.divergence_reasons[s.step_idx] = "no action recorded"
                report.early_termination_step = s.step_idx
                break
            try:
                obs, reward, done, _info = env.step(s.action)
            except Exception as exc:  # noqa: BLE001 — surface env failures
                report.steps_diverged.append(s.step_idx)
                report.divergence_reasons[s.step_idx] = (
                    f"env.step raised {type(exc).__name__}: {exc}"
                )
                report.early_termination_step = s.step_idx
                break
            report.steps_executed += 1

            reasons: list[str] = []
            # Observation comparison: only run this check when we have a
            # recorded observation to compare against. ``MockEnvironment``
            # records the observation BEFORE step (the pre-step state); the
            # runner records the same way, so step N's observation matches
            # step N's pre-step state. In the Phase 1 mock-env happy path,
            # the recorded observation for step N is the pre-step state
            # (step counter = N). After env.step() the counter is N+1 — so
            # we expect mismatch on step counters and only flag if OTHER
            # fields disagree. Filter known-progressing fields.
            if (
                s.observation is not None
                and not _shallow_equal(obs, s.observation)
                and not _equal_modulo_progress(obs, s.observation)
            ):
                reasons.append(f"observation diverged: env={obs!r} recorded={s.observation!r}")

            if s.reward is not None and not _floats_close(reward, s.reward):
                reasons.append(f"reward diverged: env={reward} recorded={s.reward}")

            if s.success_flag and not done:
                reasons.append(
                    "done flag diverged: recorded success but env.step returned done=False"
                )

            if reasons:
                report.steps_diverged.append(s.step_idx)
                report.divergence_reasons[s.step_idx] = "; ".join(reasons)

        # Best-effort shutdown.
        with contextlib.suppress(Exception):
            env.shutdown()

        return report

    # --- Internals -----------------------------------------------------------

    def _load_cot(self) -> list[dict[str, Any]]:
        if self._cot_events is None:
            self._cot_events = _read_jsonl(self.cot_path)
        return self._cot_events

    def _load_metadata(self) -> list[dict[str, Any]]:
        if self._metadata_events is None:
            self._metadata_events = _read_jsonl(self.metadata_path)
        return self._metadata_events

    def _load_inputs(self) -> list[dict[str, Any]]:
        if self._input_events is None:
            self._input_events = _read_jsonl(self.inputs_path)
        return self._input_events

    @staticmethod
    def _find_event_args_at(events: list[dict[str, Any]], target_ts: float, event_type: str) -> Any:
        """Return ``event_args`` of the first matching co-timestamped event."""
        for ev in events:
            if ev.get("event_type") != event_type:
                continue
            ts = ev.get("timestamp")
            if not isinstance(ts, (int, float)):
                continue
            if abs(float(ts) - target_ts) <= _TIMESTAMP_EPSILON_SEC:
                return ev.get("event_args")
        return None

    @staticmethod
    def _find_thinking_for_step(events: list[dict[str, Any]], step_idx: int) -> Any:
        """LLM_THINKING carries an explicit step field — match on that."""
        for ev in events:
            if ev.get("event_type") != EVENT_LLM_THINKING:
                continue
            args = ev.get("event_args") or {}
            if args.get("step") == step_idx:
                return args
        return None

    @staticmethod
    def _stream_start_timestamp(events: list[dict[str, Any]]) -> float:
        for ev in events:
            if ev.get("event_type") == EVENT_START:
                ts = ev.get("timestamp")
                if isinstance(ts, (int, float)):
                    return float(ts)
        return 0.0

    @staticmethod
    def _stream_event_timestamp(events: list[dict[str, Any]], event_type: str) -> float | None:
        for ev in events:
            if ev.get("event_type") == event_type:
                ts = ev.get("timestamp")
                if isinstance(ts, (int, float)):
                    return float(ts)
        return None


# --- Helpers ----------------------------------------------------------------


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read a JSONL file into a list of dicts. Skips malformed lines.

    A bundle with a malformed line is reported by ``verify_consistency``
    via the missing-event paths — we don't raise here so partial bundles
    can still be inspected.
    """
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                out.append(payload)
    return out


def _floats_close(a: float | None, b: float | None, *, tol: float = 1e-6) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return abs(float(a) - float(b)) <= tol


def _shallow_equal(a: Any, b: Any) -> bool:
    """Structural equality good enough for mock-env observations."""
    if a == b:
        return True
    # Tolerate float jitter inside otherwise-equal dicts.
    if isinstance(a, dict) and isinstance(b, dict):
        if a.keys() != b.keys():
            return False
        return all(_shallow_equal(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return False
        return all(_shallow_equal(x, y) for x, y in zip(a, b, strict=False))
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return _floats_close(float(a), float(b))
    return False


def _equal_modulo_progress(env_obs: Any, recorded_obs: Any) -> bool:
    """Tolerate the known mock-env "step counter advances after step()" gap.

    The recording captures the PRE-step observation for step N (counter = N).
    Re-executing step N drives the counter to N+1. Both observations should
    agree on every field EXCEPT ``step`` (which differs by exactly 1) and
    ``last_action`` (which differs because the recording shows the previous
    step's action, while replay shows the current one).
    """
    if not (isinstance(env_obs, dict) and isinstance(recorded_obs, dict)):
        return False
    fields_to_check = set(env_obs.keys()) | set(recorded_obs.keys())
    fields_to_check -= {"step", "last_action"}
    for k in fields_to_check:
        if not _shallow_equal(env_obs.get(k), recorded_obs.get(k)):
            return False
    # Step counter must have advanced by exactly 1.
    env_step = env_obs.get("step")
    rec_step = recorded_obs.get("step")
    return not (
        isinstance(env_step, int) and isinstance(rec_step, int) and env_step != rec_step + 1
    )


__all__ = [
    "ConsistencyReport",
    "ReplayDriftReport",
    "ReplayStep",
    "Replayer",
]
