# Vendor Pricing Guide

**Document Version:** 1.0  
**Last Updated:** 2024  
**Contact:** howard.linra@gmail.com

---

## 1. Pricing Model Overview

Our pricing structure is based on **complexity tiers** derived from scene requirements. Each clip is categorized into one of three complexity levels, which determines the base rate multiplier applied.

| Complexity Tier | Description | Rate Multiplier |
|-----------------|-------------|-----------------|
| **High** | Complex multi-vehicle scenarios, dynamic weather, intricate camera movements, custom physics | 1.0× |
| **Mid** | Standard scenarios with moderate interaction, basic weather effects, standard camera work | 0.7× |
| **Low** | Simple single-vehicle scenarios, clear weather, static or simple camera movements | 0.4× |

---

## 2. Per-Clip Rates

Base rate per clip is **$X** (to be negotiated per contract). Final pricing is calculated as:

```
Final Rate = Base Rate ($X) × Complexity Multiplier
```

### Rate Examples

| Complexity | Multiplier | Rate per Clip |
|------------|------------|---------------|
| High | 1.0× | $X |
| Mid | 0.7× | $0.70X |
| Low | 0.4× | $0.40X |

> **Note:** Base rate $X is determined during contract negotiation based on tech stack, volume commitment, and project duration.

---

## 3. Volume Discounts

We offer tiered discounts based on monthly clip volume. Discounts are applied to the total monthly invoice before any rush or special clip adjustments.

| Monthly Volume | Discount |
|----------------|----------|
| 500 – 1,999 clips | 5% |
| 2,000 – 9,999 clips | 10% |
| 10,000+ clips | 15% |

### Example Calculation
- 3,000 clips/month at mid complexity ($0.70X each)
- Subtotal: 3,000 × $0.70X = $2,100X
- Volume discount (10%): -$210X
- **Final: $1,890X**

---

## 4. Rush Rates

Expedited delivery incurs additional charges based on turnaround time:

| Turnaround | Multiplier |
|------------|------------|
| Standard (7+ business days) | 1.0× |
| **48-hour delivery** | **2.0×** |
| **24-hour delivery** | **3.0×** |

> Rush rates are applied per batch or project, not per individual clip within a standard delivery order.

---

## 5. Special Clips

Certain clip types require additional processing or specialized handling:

### Route Types Requiring Surcharge

| Route Type | Description | Surcharge |
|------------|-------------|-----------|
| `route_type=2` | Specific scene requirements (custom locations, landmarks) | +50% |
| `route_type=3` | Advanced scenarios (multi-agent, rare events, edge cases) | +50% |

### Special Clip Categories
- **Custom weather combinations** (e.g., rain + fog + night)
- **Specific vehicle configurations** not in standard library
- **Precise timing/synchronization requirements**
- **Multi-camera angle sequences**

```
Special Clip Rate = Base Rate × Complexity × 1.5
```

---

## 6. Re-Record Policy

We maintain a **quality-first** approach with fair terms:

| Scenario | Policy |
|----------|--------|
| **Failed lint validation** | No charge to vendor; vendor must re-record at no additional cost |
| **Client-requested changes** | Negotiated based on scope |
| **Technical issues (vendor side)** | Vendor re-records at no charge |
| **Specification changes (client side)** | New quote required |

### Lint Validation Includes
- Frame rate consistency
- Resolution compliance
- Metadata accuracy
- Scene requirement matching
- File format validation

> **Important:** Clips that pass lint but fail subjective review will be discussed case-by-case. We value long-term partnerships and fair resolution.

---

## 7. Payment Terms

| Milestone | Percentage | Timing |
|-----------|------------|--------|
| **Advance payment** | 30% | Upon signed SOW |
| **Final payment** | 70% | Within 7 business days of acceptance |

### Payment Schedule
1. Invoice submitted upon delivery
2. Review period: 3 business days
3. Acceptance or revision request
4. Final payment due: 7 business days from acceptance

### Payment Methods
- Bank transfer (preferred)
- PayPal (for amounts under $5,000)
- Other methods by arrangement

---

## 8. Quote Request Template

Please complete the following template when requesting a quote:

```
================================================================================
VENDOR QUOTE REQUEST
================================================================================

Vendor name: 
    [Your company/individual name]

Estimated monthly capacity (clips): 
    [Number of clips you can reliably deliver per month]

Tech stack: 
    [ ] Minecraft
    [ ] CS2
    [ ] BeamNG
    [ ] Unity
    [ ] Unreal Engine
    [ ] Other: _______________

GPU machines available: 
    [Number and specifications, e.g., "4x RTX 4090, 2x RTX 3090"]

Operator count: 
    [Number of trained operators who can work on the project]

Earliest start date: 
    [YYYY-MM-DD]

Special requirements: 
    [Any constraints, preferences, or requirements]

Additional notes:
    [Any other relevant information]

================================================================================
```

---

## 9. Sample Quote

Below is a filled example for reference:

```
================================================================================
VENDOR QUOTE REQUEST
================================================================================

Vendor name: 
    PixelForge Studios

Estimated monthly capacity (clips): 
    2,500 clips

Tech stack: 
    [X] Minecraft
    [ ] CS2
    [X] BeamNG
    [X] Unity
    [ ] Unreal Engine
    [ ] Other: _______________

GPU machines available: 
    6x RTX 4090, 4x RTX 3080, 2x A6000

Operator count: 
    8 full-time operators (2 senior, 6 junior)

Earliest start date: 
    2024-02-15

Special requirements: 
    - Prefer 48-hour minimum turnaround for standard clips
    - Can accommodate rush orders with 24-hour notice
    - Available for weekend work with 1.5x weekend rate

Additional notes:
    - 3 years experience with game capture
    - In-house QA team for pre-delivery validation
    - Can scale to 4,000 clips/month with 2-week notice

================================================================================
```

### Sample Pricing Calculation

| Item | Calculation | Amount |
|------|-------------|--------|
| Base rate (negotiated) | - | $5.00/clip |
| Mid-complexity clips (1,500) | 1,500 × $5.00 × 0.7 | $5,250 |
| High-complexity clips (800) | 800 × $5.00 × 1.0 | $4,000 |
| Low-complexity clips (200) | 200 × $5.00 × 0.4 | $400 |
| **Subtotal** | | **$9,650** |
| Volume discount (2,500 clips) | 10% | -$965 |
| **Monthly Total** | | **$8,685** |

---

## 10. Contact Information

For quotes, questions, or to begin the onboarding process:

**Primary Contact:**  
Howard Lin  
Email: **howard.linra@gmail.com**

### Response Times
| Request Type | Response Time |
|--------------|---------------|
| Quote requests | 48 hours |
| SOW delivery | 48 hours from approved quote |
| General inquiries | 24–48 hours |
| Urgent matters | Same business day |

### Onboarding Process
1. Submit quote request template
2. Receive preliminary pricing (48h)
3. Technical capability assessment
4. Pilot project (50–100 clips)
5. Full SOW and contract
6. Advance payment → Production begins

---

## Summary Quick Reference

| Category | Rate/Discount |
|----------|---------------|
| High complexity | 1.0× base |
| Mid complexity | 0.7× base |
| Low complexity | 0.4× base |
| 500+ clips/month | 5% discount |
| 2,000+ clips/month | 10% discount |
| 10,000+ clips/month | 15% discount |
| 48h rush | 2.0× |
| 24h rush | 3.0× |
| Special clips (route_type 2/3) | +50% |
| Failed lint | No charge, re-record |
| Payment terms | 30% advance + 70% on acceptance |

---

*We look forward to a productive partnership. Please reach out with any questions or to request a customized quote.*