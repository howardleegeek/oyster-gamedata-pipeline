"""Red Team — adversarial attack catalog for BFT N=4 stress-testing.

Each attack mutates a baseline PRD-compliant frame in a specific way. The
Blue Team scoring harness runs every attack against every verifier and
reports which attacks slip through (architectural blind spots).

Buckets:
  A — single-field mutation (FI-01..FI-05, in bft_adversarial_harness.py)
  B — multi-field coordinated mutation (this module: B-01..B-05)
  C — sub-threshold perturbation (this module: C-01..C-05)
  D — replay/temporal attacks (this module: D-01..D-05)
"""
from .attackers import (
    ATTACK_CATALOG,
    AttackCase,
    AttackResult,
    bucket_b_attacks,
    bucket_c_attacks,
    bucket_d_attacks,
)

__all__ = [
    "AttackResult",
    "AttackCase",
    "ATTACK_CATALOG",
    "bucket_b_attacks",
    "bucket_c_attacks",
    "bucket_d_attacks",
]
