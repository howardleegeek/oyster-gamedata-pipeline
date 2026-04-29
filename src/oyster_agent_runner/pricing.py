"""Provider pricing tables for the `quote` CLI subcommand.

Single source of truth for per-million-token (MTok) prices used by
`quote.py` to project trajectory cost. Prices here are the *list* prices
quoted on the provider's public pricing page; volume discounts, batch
API discounts, and caching savings are NOT applied — `quote.py` reports
both a low and high estimate so the sales-grade output stays honest.

Adding a provider
-----------------
Append a new `ProviderPricing` row to `PROVIDER_PRICING`. The `key` is
what the user passes via `--provider`. `model_label` is the
human-readable name printed in the quote header. `input_price_per_mtok`
and `output_price_per_mtok` are USD per 1,000,000 tokens.

Note on extended thinking
-------------------------
For Claude with extended thinking, the thinking tokens are billed as
*output tokens*. We model this in `quote.py` by adding the thinking
budget to the per-step output token count — no special-casing here.

Price sources (verified 2026-04-28)
-----------------------------------
- Anthropic Claude (Sonnet 4 / 4.5):
  https://www.anthropic.com/pricing
  Sonnet 4 and Sonnet 4.5 both list at $3 / MTok input, $15 / MTok output.
- OpenAI thinking models (GPT-5 / o-series stub):
  https://openai.com/api/pricing
  We use a conservative reasoning-tier estimate ($20 in / $50 out per
  MTok). This is a *stub* — when GPT-5 thinking pricing settles, update
  the row and bump `confidence` in the quote.
"""

from __future__ import annotations

from dataclasses import dataclass

# 4× cost → unit price. Tunable by the sales operator: bump for premium
# customers, dial back for design-partner pricing. We expose it as a
# module constant (not a CLI flag) because it's a business-policy lever,
# not a per-quote option — change it deliberately, in code review.
DEFAULT_MARGIN_MULTIPLIER: float = 4.0

# Token-per-character heuristic. 1 token ≈ 3.7 chars across English +
# code in the cl100k / tiktoken tokenizer family; the inverse is ~0.27
# tokens/char. This is the same constant Engineer Y used to derive the
# ~$13 / 50-step claim in `docs/PHASE1_RUNBOOK.md`. Keep it here so
# `quote.py` and any future calibration script share one number.
TOKENS_PER_CHAR: float = 0.27


@dataclass(frozen=True)
class ProviderPricing:
    """Pricing record for one (provider, model) pair.

    All prices are USD per 1,000,000 tokens. We keep the model id (what
    the API call sends) and a human-readable label separate so the quote
    header reads cleanly without leaking model-id sprawl.
    """

    key: str
    model_id: str
    model_label: str
    input_price_per_mtok: float
    output_price_per_mtok: float
    supports_thinking: bool
    notes: str


PROVIDER_PRICING: dict[str, ProviderPricing] = {
    "claude-thinking": ProviderPricing(
        key="claude-thinking",
        model_id="claude-sonnet-4-5",
        model_label="Sonnet 4.5",
        input_price_per_mtok=3.0,
        output_price_per_mtok=15.0,
        supports_thinking=True,
        # Anthropic bills extended-thinking tokens at the output rate; we
        # collapse thinking + final answer into one output total in
        # quote.py rather than reporting them as two cost lines (cleaner
        # for the sales sheet).
        notes="Anthropic Claude Sonnet 4.5 with extended thinking; thinking billed as output.",
    ),
    "claude-vision": ProviderPricing(
        key="claude-vision",
        model_id="claude-sonnet-4",
        model_label="Sonnet 4 (vision)",
        input_price_per_mtok=3.0,
        output_price_per_mtok=15.0,
        supports_thinking=False,
        notes="Anthropic Claude Sonnet 4 with image input. Image tokenization not modeled.",
    ),
    "gpt-thinking-stub": ProviderPricing(
        key="gpt-thinking-stub",
        model_id="gpt-thinking-stub",
        model_label="GPT thinking (stub)",
        input_price_per_mtok=20.0,
        output_price_per_mtok=50.0,
        supports_thinking=True,
        notes="Conservative placeholder for OpenAI reasoning-tier models; recalibrate.",
    ),
}


def get_pricing(provider_key: str) -> ProviderPricing:
    """Look up a pricing record. Raises `KeyError` on unknown providers.

    The CLI catches the `KeyError` and re-raises as a `typer.BadParameter`
    so the user sees a clean error instead of a stack trace.
    """
    if provider_key not in PROVIDER_PRICING:
        raise KeyError(provider_key)
    return PROVIDER_PRICING[provider_key]


def list_provider_keys() -> list[str]:
    """Stable, sorted list of supported provider keys for help text."""
    return sorted(PROVIDER_PRICING.keys())


__all__ = [
    "DEFAULT_MARGIN_MULTIPLIER",
    "PROVIDER_PRICING",
    "ProviderPricing",
    "TOKENS_PER_CHAR",
    "get_pricing",
    "list_provider_keys",
]
