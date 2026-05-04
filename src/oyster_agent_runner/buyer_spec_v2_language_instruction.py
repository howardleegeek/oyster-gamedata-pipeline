#!/usr/bin/env python3
"""
buyer_spec_v2_language_instruction.py
======================================

Cluster A: per-episode instruction string + optional dense narration.
Required by RT-2 / OpenVLA / Octo / Pi-0 — unlocks VLA fine-tune ecosystem.

Generates language instruction specs for vision-language-action (VLA) models,
supporting both minimal task prompts and dense step-by-step narration modes.

Usage:
    python buyer_spec_v2_language_instruction.py --task "pick up the red block"
    python buyer_spec_v2_language_instruction.py --batch episodes.json --out out.yaml
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Lazy imports for optional heavy deps
_YAML: Any = None


def _get_yaml() -> Any:
    """Lazy-load PyYAML; raise ImportError if unavailable."""
    global _YAML
    if _YAML is None:
        import yaml as _YAML  # noqa: PLC0415
    return _YAML


# ---------------------------------------------------------------------------
# Core generator
# ---------------------------------------------------------------------------

VLA_MODELS = ("rt2", "openvla", "octo", "pi0")

DEFAULT_TEMPLATES: Dict[str, str] = {
    "minimal": "Perform: {task}",
    "dense": "Execute: {task}. Follow these steps carefully.",
    "verbose": "Complete the following task with full narration: {task}",
}


class LanguageInstructionGenerator:
    """Generate per-episode language instructions for VLA fine-tuning."""

    def __init__(self, config_path: Optional[str] = None) -> None:
        self._templates: Dict[str, str] = dict(DEFAULT_TEMPLATES)
        if config_path:
            self._load_config(config_path)

    # -- private helpers ----------------------------------------------------

    def _load_config(self, path: str) -> None:
        """Load optional JSON/YAML config to override templates."""
        p = Path(path)
        with open(p, "r", encoding="utf-8") as fh:
            if p.suffix.lower() in (".yaml", ".yml"):
                yaml_mod = _get_yaml()
                data = yaml_mod.safe_load(fh)
            else:
                data = json.load(fh)
        if isinstance(data, dict) and "templates" in data:
            self._templates.update(data["templates"])

    # -- public API ---------------------------------------------------------

    def generate(
        self,
        episode_id: str,
        task: str,
        mode: str = "minimal",
        dense_narration: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Build a single-episode instruction spec.

        Args:
            episode_id: Unique episode identifier.
            task: Natural-language task description.
            mode: One of 'minimal', 'dense', 'verbose'.
            dense_narration: Optional explicit narration string.

        Returns:
            Dict compatible with VLA fine-tune data loaders.
        """
        template = self._templates.get(mode, self._templates["minimal"])
        instruction = template.format(task=task)

        spec: Dict[str, Any] = {
            "episode_id": episode_id,
            "instruction": instruction,
            "task": task,
            "narration_mode": mode,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "vla_compatible": {m: True for m in VLA_MODELS},
        }

        if dense_narration or mode in ("dense", "verbose"):
            spec["dense_narration"] = dense_narration or (
                f"Detailed step-by-step narration for: {task}"
            )

        return spec

    def batch_generate(
        self,
        episodes: List[Dict[str, Any]],
        output: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Generate instructions for a batch of episodes.

        Args:
            episodes: List of dicts with keys matching ``generate()`` params.
            output: Optional file path to write results (JSON or YAML).

        Returns:
            List of generated instruction specs.
        """
        results = [self.generate(**ep) for ep in episodes]

        if output:
            p = Path(output)
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "w", encoding="utf-8") as fh:
                if p.suffix.lower() in (".yaml", ".yml"):
                    yaml_mod = _get_yaml()
                    yaml_mod.dump(results, fh, default_flow_style=False)
                else:
                    json.dump(results, fh, indent=2)

        return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    """Construct the argparse CLI parser."""
    parser = argparse.ArgumentParser(
        description="Generate VLA language instruction specs (Cluster A).",
    )
    parser.add_argument(
        "--task", type=str, default=None,
        help="Single task description (for one-off generation).",
    )
    parser.add_argument(
        "--episode-id", type=str, default="ep_001",
        help="Episode identifier (default: ep_001).",
    )
    parser.add_argument(
        "--mode", type=str, default="minimal",
        choices=("minimal", "dense", "verbose"),
        help="Narration density level.",
    )
    parser.add_argument(
        "--narration", type=str, default=None,
        help="Explicit dense narration string.",
    )
    parser.add_argument(
        "--batch", type=str, default=None,
        help="Path to JSON file with episode list for batch mode.",
    )
    parser.add_argument(
        "--config", type=str, default=None,
        help="Path to JSON/YAML config with custom templates.",
    )
    parser.add_argument(
        "--out", type=str, default=None,
        help="Output file path (JSON or YAML).",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Entry-point with argparse CLI. Returns 0 on success, 1 on error."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    gen = LanguageInstructionGenerator(config_path=args.config)

    try:
        if args.batch:
            batch_path = Path(args.batch)
            with open(batch_path, "r", encoding="utf-8") as fh:
                episodes = json.load(fh)
            results = gen.batch_generate(episodes, output=args.out)
        elif args.task:
            spec = gen.generate(
                episode_id=args.episode_id,
                task=args.task,
                mode=args.mode,
                dense_narration=args.narration,
            )
            results = [spec]
        else:
            parser.print_help()
            return 1
    except (FileNotFoundError, json.JSONDecodeError, KeyError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if not args.out:
        print(json.dumps(results, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
