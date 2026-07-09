#!/usr/bin/env python3
"""
G152 · bin/reward_signal_provider.py

Cluster C: Per-step sparse reward float + per-episode task_success bool.

Provides reward signal computation for DreamerV3-family world-model and
RL training pipelines. Computes sparse rewards per-step and determines
task success at episode termination.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

logger = logging.getLogger(__name__)

_numpy = None


def _get_numpy():
    """Lazy import numpy to avoid mandatory dependency."""
    global _numpy
    if _numpy is None:
        import numpy as np
        _numpy = np
    return _numpy


@dataclass
class RewardConfig:
    """Configuration for reward signal computation."""
    episode_length: int = 1000
    success_threshold: float = 0.95
    sparse_reward_scale: float = 1.0
    terminal_reward: float = 1.0
    failure_penalty: float = -0.1
    reward_shaping: bool = False
    shaping_scale: float = 0.01


@dataclass
class EpisodeState:
    """Tracks state for a single episode."""
    step_count: int = 0
    cumulative_reward: float = 0.0
    task_progress: float = 0.0
    done: bool = False
    success: bool = False
    rewards: List[float] = field(default_factory=list)


class RewardSignalProvider:
    """Provides per-step sparse rewards and per-episode success signals.
    
    Implements reward computation compatible with DreamerV3-family world models.
    Supports sparse reward signals, task success determination, and optional
    reward shaping for dense feedback.
    """
    
    def __init__(self, config: Optional[RewardConfig] = None) -> None:
        self.config = config or RewardConfig()
        self._episode: Optional[EpisodeState] = None
        self._episode_count: int = 0
    
    def reset(self) -> Tuple[float, Dict[str, Any]]:
        """Reset for a new episode. Returns (initial_reward, info_dict)."""
        self._episode = EpisodeState()
        self._episode_count += 1
        return 0.0, {"episode_id": self._episode_count, "max_steps": self.config.episode_length}
    
    def step(
        self,
        task_progress: float = 0.0,
        is_terminal: bool = False,
        is_success: bool = False,
        shaping_signal: float = 0.0,
    ) -> Tuple[float, bool, Dict[str, Any]]:
        """Compute reward for a single step.
        
        Args:
            task_progress: Current progress toward goal (0.0-1.0).
            is_terminal: Whether this is a terminal state.
            is_success: Whether the task succeeded (only valid if terminal).
            shaping_signal: Optional dense shaping signal.
        
        Returns:
            Tuple of (reward, done, info) with sparse reward and episode status.
        """
        if self._episode is None:
            self.reset()
        assert self._episode is not None
        
        self._episode.step_count += 1
        self._episode.task_progress = task_progress
        reward = 0.0
        
        if self.config.reward_shaping:
            reward += shaping_signal * self.config.shaping_scale
        
        done = is_terminal or self._episode.step_count >= self.config.episode_length
        
        if done:
            success = is_success or task_progress >= self.config.success_threshold
            self._episode.success = success
            self._episode.done = True
            reward += (self.config.terminal_reward if success else self.config.failure_penalty)
            reward *= self.config.sparse_reward_scale
        
        self._episode.cumulative_reward += reward
        self._episode.rewards.append(reward)
        
        info = {
            "step": self._episode.step_count,
            "progress": task_progress,
            "cumulative_reward": self._episode.cumulative_reward,
            "success": self._episode.success if done else None,
        }
        return reward, done, info
    
    def get_success(self) -> bool:
        """Get whether the current/last episode was successful."""
        return self._episode.success if self._episode else False
    
    def get_episode_summary(self) -> Dict[str, Any]:
        """Get summary statistics for the current/last episode."""
        if self._episode is None:
            return {"error": "No episode data available"}
        
        np = _get_numpy()
        rewards = np.array(self._episode.rewards) if self._episode.rewards else np.array([0.0])
        
        return {
            "episode_id": self._episode_count,
            "steps": self._episode.step_count,
            "success": self._episode.success,
            "cumulative_reward": self._episode.cumulative_reward,
            "mean_reward": float(np.mean(rewards)),
            "std_reward": float(np.std(rewards)),
        }
    
    def generate_trajectory(
        self, progress_sequence: Iterator[float], success_at_end: bool = True
    ) -> Iterator[Tuple[float, bool, bool]]:
        """Generate rewards for a trajectory of progress values."""
        self.reset()
        progress_list = list(progress_sequence)
        
        for i, progress in enumerate(progress_list):
            is_terminal = (i == len(progress_list) - 1)
            reward, done, _ = self.step(
                task_progress=progress, is_terminal=is_terminal, is_success=success_at_end and is_terminal
            )
            yield reward, done, self._episode.success if done else False


def load_config_from_file(path: Path) -> RewardConfig:
    """Load configuration from a YAML or JSON file."""
    content = path.read_text()
    
    if path.suffix in (".yaml", ".yml"):
        try:
            import yaml
            data = yaml.safe_load(content)
        except ImportError as exc:
            logger.debug(
                "reward_signal_provider: PyYAML not installed, "
                "falling back to JSON parser for %s: %s",
                path, exc,
            )
            data = json.loads(content)
    else:
        data = json.loads(content)
    
    return RewardConfig(
        episode_length=data.get("episode_length", 1000),
        success_threshold=data.get("success_threshold", 0.95),
        sparse_reward_scale=data.get("sparse_reward_scale", 1.0),
        terminal_reward=data.get("terminal_reward", 1.0),
        failure_penalty=data.get("failure_penalty", -0.1),
        reward_shaping=data.get("reward_shaping", False),
        shaping_scale=data.get("shaping_scale", 0.01),
    )


def main(argv: Optional[List[str]] = None) -> int:
    """Main entry point for CLI execution."""
    parser = argparse.ArgumentParser(
        description="Reward signal provider for DreamerV3-family RL training.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--config", "-c", type=Path, help="Path to YAML/JSON config file")
    parser.add_argument("--episode-length", "-n", type=int, default=1000, help="Max steps per episode")
    parser.add_argument("--success-threshold", "-t", type=float, default=0.95, help="Success threshold")
    parser.add_argument("--output", "-o", type=Path, help="Output file for episode summary (JSONL)")
    parser.add_argument("--demo", action="store_true", help="Run demonstration with simulated trajectory")
    
    args = parser.parse_args(argv)
    
    config = load_config_from_file(args.config) if args.config and args.config.exists() else RewardConfig(
        episode_length=args.episode_length, success_threshold=args.success_threshold
    )
    
    provider = RewardSignalProvider(config)
    
    if args.demo:
        print(f"Demo: episode_length={config.episode_length}, threshold={config.success_threshold}")
        np = _get_numpy()
        progress_values = np.linspace(0, 1.0, config.episode_length)
        
        provider.reset()
        for i, progress in enumerate(progress_values):
            is_terminal = (i == len(progress_values) - 1)
            reward, done, info = provider.step(
                task_progress=float(progress), is_terminal=is_terminal, is_success=True
            )
            if done:
                print(f"Episode done: step={info['step']}, success={provider.get_success()}")
                break
        
        summary = provider.get_episode_summary()
        print("Summary:", json.dumps(summary, indent=2))
    
    if args.output:
        summary = provider.get_episode_summary()
        with args.output.open("w") as f:
            f.write(json.dumps(summary) + "\n")
        print(f"Written to {args.output}")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
