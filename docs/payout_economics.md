# Payout Economics

## Per-Session Pricing Breakdown

### Base Rate
- **$5.00** per qualified session (audit score ≥ 101)

### Multipliers

| Factor | Multiplier | Conditions |
|--------|------------|------------|
| Route Type 3-4 | 1.5x | Advanced/complex routes |
| Multi-Player Session | 2.0x | Sessions with multiple participants |
| Novel Scene | 1.3x | Unique or novel scene content |

### Quality Bonuses

| Depth Source | Bonus |
|--------------|-------|
| `engine_zbuffer` | +$2.00 |
| `monocular_da_v2` | +$0.00 |

### Example Calculations

**Basic Session (Route 1, solo, standard scene)**
```
$5.00 × 1.0 (route 1) × 1.0 (solo) × 1.0 (standard) + $0 (monocular) = $5.00
```

**Advanced Multi-Player Session (Route 4, multiplayer, novel scene, zbuffer)**
```
$5.00 × 1.5 (route 4) × 2.0 (multiplayer) × 1.3 (novel) + $2.00 (zbuffer) = $23.50
```

**Complex Route (Route 3, solo, novel scene, monocular)**
```
$5.00 × 1.5 (route 3) × 1.0 (solo) × 1.3 (novel) + $0 (monocular) = $9.75
```

## Buyer Billing Model

### Session Pricing
- Buyers pay per session based on route complexity, duration, and quality
- Typical buyer price: **$15-50 per session** (depends on route length and quality tier)
- Oyster charges a **20% platform fee** on gross revenue

### Revenue Split

```
Buyer Payment:     $25.00
─────────────────────────────
Oyster Platform:   $5.00 (20%)
Contributor:      $20.00 (80%)
```

### Oyster Margin Analysis

| Component | Amount | Notes |
|-----------|--------|-------|
| Buyer pays | $25.00 | Session fee |
| Platform fee (20%) | $5.00 | Oyster revenue |
| Contributor payout | $20.00 | 80% to contributor |

**Effective contributor share: 80% of gross**

## Anti-Fraud Measures

### Daily Cap
- **$200 per contributor per day** maximum payout
- Prevents abuse through repeated small sessions
- Calculated based on UTC date

### Audit Threshold
- Sessions must score **≥ 101** on audit to qualify for payout
- Ensures quality threshold is met
- Prevents low-quality spam

### Idempotency
- Every payout has unique idempotency key
- Re-running cron never double-pays
- Session IDs tracked to prevent duplicate queueing

## Payment Methods

### Primary: Stripe Connect Express
- **Countries**: 25+ supported (US, UK, EU, CA, AU, JP, etc.)
- **Onboarding**: Contributor self-serves via Stripe Express
- **KYC**: Handled by Stripe (not stored by Oyster)
- **Payout timing**: Instant to 2 business days
- **Minimum**: $0.50

### Fallback: PayPal Payouts
- **Countries**: 100+ supported
- **Use case**: Contributors in Stripe-unsupported regions
- **Payout timing**: Instant to 3 business days
- **Minimum**: $1.00

## Payout Schedule

### Automatic
- Payouts queued when buyer approves session
- Cron runs every 6 hours to process queue
- Retries up to 3 times on transient failures

### Manual Withdrawal
- Contributors can request withdrawal anytime
- Available balance must exceed minimum
- Processed within 24 hours

## Tax Considerations

- **1099-K**: Stripe issues for US contributors > $600/year
- **International**: Varies by country
- Oyster does not withhold taxes (contributor responsibility)

## Cost Structure Summary

| Item | Amount |
|------|--------|
| Base session payout | $5.00 |
| Max session payout | ~$23.50 |
| Daily cap | $200.00 |
| Platform margin | 20% |
| Stripe fees | ~2.9% + $0.30 (absorbed) |
| PayPal fees | ~2.5% (absorbed) |

## Future Considerations

- Volume discounts for high-quality contributors
- Tiered rates based on contributor rating
- Instant payouts (premium feature)
- Multi-currency support
