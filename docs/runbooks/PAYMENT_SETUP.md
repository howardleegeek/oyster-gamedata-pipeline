# G201 · Vendor Payment Setup Runbook

**Module:** `docs/runbooks/PAYMENT_SETUP.md`  
**Purpose:** Vendor payout setup — Stripe Connect for US vendors, manual ACH/wire 
spreadsheet for non-US vendors, and tax form (W-9/W-8BEN) collection.  
**Owner:** Finance Operations  
**Last Updated:** 2025-01-15  

---

## 1. Overview

Vendor payments are processed through two channels:

| Channel        | Region   | Method                | Tax Form      |
|----------------|----------|-----------------------|---------------|
| Stripe Connect | US       | Automated API payout  | W-9           |
| Manual ACH/Wire| Non-US   | Spreadsheet → Bank    | W-8BEN/W-8BEN-E |

**Core Principle:** No payments without verified tax forms.

---

## 2. Prerequisites

### 2.1 Required Access

- Stripe Dashboard (Connect module enabled)
- Bank portal credentials (ACH/wire initiation)
- Vendor database (read/write access)
- Secure storage for tax forms (S3/GCS or equivalent)

### 2.2 Environment Variables

```bash
# Stripe (use restricted keys in production)
export STRIPE_SECRET_KEY="sk_live_..."
export STRIPE_WEBHOOK_SECRET="whsec_..."

# Database connection
export VENDOR_DB_URL="postgresql://user:pass@host:5432/vendor_db"

# Secure storage
export TAX_FORM_BUCKET="vendor-tax-forms"
```

**Security Note:** Never commit credentials to version control. Use a secrets manager 
in production environments.

---

## 3. Tax Form Collection

### 3.1 Form Selection Matrix

| Vendor Type | Tax Residence | Required Form | Validity Period |
|-------------|---------------|---------------|-----------------|
| Individual  | US            | W-9           | Indefinite      |
| Business    | US            | W-9           | Indefinite      |
| Individual  | Non-US        | W-8BEN        | 3 years         |
| Business    | Non-US        | W-8BEN-E      | 3 years         |

### 3.2 Collection Workflow

1. **Send Request:** Email vendor a secure portal link with appropriate form template
2. **Vendor Action:** Vendor completes, signs, and uploads PDF
3. **Validation:** Finance validates all required fields, signature, date, and tax ID format
4. **Storage:** Save to `s3://{bucket}/tax-forms/{vendor_id}/{form_type}_{date}.pdf`
5. **Database Update:** Set `tax_form_status = 'verified'` and `tax_form_expiry_date`

### 3.3 Validation Checklist

**W-9 Forms:**
- [ ] Legal name matches vendor record exactly
- [ ] Tax classification selected (individual, C-Corp, etc.)
- [ ] TIN provided (SSN: XXX-XX-XXXX or EIN: XX-XXXXXXX format)
- [ ] Address matches vendor record
- [ ] Signature present and dated

**W-8BEN Forms (Individuals):**
- [ ] Full legal name provided
- [ ] Country of citizenship specified
- [ ] Foreign tax ID or SSN/ITIN provided
- [ ] Permanent residence address outside US
- [ ] Signature present and dated within validity period

**W-8BEN-E Forms (Entities):**
- [ ] Organization name matches vendor record
- [ ] Country of incorporation specified
- [ ] Foreign tax ID provided
- [ ] Chapter 3 status claimed (entity type)
- [ ] Signature of authorized signatory present

### 3.4 Expiry Monitoring

Run monthly check for expiring W-8BEN/W-8BEN-E forms:

```sql
SELECT vendor_id, vendor_name, tax_form_expiry_date
FROM vendors
WHERE tax_form_status = 'verified'
  AND tax_form_expiry_date < CURRENT_DATE + INTERVAL '90 days'
ORDER BY tax_form_expiry_date;
```

Send renewal reminders at 90, 60, and 30 days before expiry.

---

## 4. US Vendor Setup (Stripe Connect)

### 4.1 Create Connected Account

```python
#!/usr/bin/env python3
"""Stripe Connect onboarding for US vendors."""

import argparse
import json
import os
import sys
from datetime import datetime
from typing import Dict, Any


def get_stripe():
    """Lazy import stripe module to avoid mandatory dependency."""
    import stripe
    stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
    return stripe


def create_vendor_account(
    vendor_id: str,
    email: str,
    business_name: str,
    country: str = "US"
) -> Dict[str, Any]:
    """
    Create a Stripe Connect account for a US vendor.
    
    Args:
        vendor_id: Internal vendor identifier
        email: Vendor contact email
        business_name: Legal business name
        country: ISO country code (default: US)
    
    Returns:
        Dict containing account_id and onboarding_url
    """
    stripe = get_stripe()
    
    account = stripe.Account.create(
        type="express",
        country=country,
        email=email,
        business_profile={"name": business_name, "mcc": "5734"},
        metadata={"vendor_id": vendor_id, "created_at": datetime.utcnow().isoformat()}
    )
    
    onboarding = stripe.AccountLink.create(
        account=account.id,
        refresh_url="https://vendor.example.com/retry",
        return_url="https://vendor.example.com/complete",
        type="account_onboarding"
    )
    
    return {"account_id": account.id, "onboarding_url": onboarding.url}


def main(argv: list = None) -> int:
    """CLI entry point for vendor account creation."""
    parser = argparse.ArgumentParser(description="Create Stripe Connect account")
    parser.add_argument("--vendor-id", required=True, help="Internal vendor ID")
    parser.add_argument("--email", required=True, help="Vendor email")
    parser.add_argument("--business-name", required=True, help="Legal business name")
    parser.add_argument("--country", default="US", help="ISO country code")
    
    args = parser.parse_args(argv)
    result = create_vendor_account(args.vendor_id, args.email, args.business_name, args.country)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

### 4.2 Onboarding Steps

1. **Create Account:** Run script above or use Stripe Dashboard
2. **Send Link:** Email onboarding URL to vendor
3. **Vendor Completes:** Vendor enters bank details, verifies identity
4. **Webhook Confirmation:** Listen for `account.updated` webhook
5. **Verify Status:** Confirm `account.payouts_enabled = True`
6. **Update Database:** Set `stripe_account_id` and `payment_status = 'active'`

### 4.3 Payout Initiation

```python
def initiate_payout(stripe_account_id: str, amount_cents: int, currency: str = "usd"):
    """Initiate payout to connected Stripe account."""
    stripe = get_stripe()
    return stripe.Payout.create(
        amount=amount_cents,
        currency=currency,
        stripe_account=stripe_account_id
    )
```

---

## 5. Non-US Vendor Setup (Manual ACH/Wire)

### 5.1 Spreadsheet Template

| Column           | Description                          | Example              |
|------------------|--------------------------------------|----------------------|
| vendor_id        | Internal vendor identifier           | VND-00123            |
| vendor_name      | Legal business name                  | Acme Ltd.            |
| country          | ISO country code                     | GB                   |
| bank_name        | Beneficiary bank name                | Barclays Bank        |
| swift_bic        | SWIFT/BIC code                       | BARCGB22             |
| account_number   | IBAN or account number               | GB29...              |
| currency         | Payment currency                     | EUR                  |
| tax_form_status  | verified/pending/expired             | verified             |

### 5.2 Payment Workflow

1. **Verify Tax Form:** Confirm `tax_form_status = 'verified'` and not expired
2. **Add to Batch:** Enter payment details in weekly batch spreadsheet
3. **Approval:** Finance manager reviews and approves batch
4. **Submit:** Upload to bank portal or send wire instructions
5. **Confirm:** Record confirmation number and update `last_payment` date
6. **Archive:** Move processed batch to archive folder with date stamp

### 5.3 File Naming Convention

```
payments_{YYYY}_{MM}_{DD}_{batch_number}.xlsx
```

### 5.4 Security Requirements

- Store spreadsheets in encrypted storage (S3 with SSE-KMS or equivalent)
- Limit access to Finance team via IAM policies
- Enable audit logging for all file access

---

## 6. Troubleshooting

### 6.1 Stripe Connect Issues

| Issue                    | Solution                              |
|--------------------------|---------------------------------------|
| Account not verified     | Check outstanding requirements in Stripe |
| Payouts disabled         | Verify bank account and identity docs |
| Webhook not received     | Check webhook secret and endpoint     |
| Invalid API key          | Verify key format and permissions     |

### 6.2 Tax Form Issues

| Issue                    | Solution                              |
|--------------------------|---------------------------------------|
| Missing signature        | Return to vendor for completion       |
| Expired W-8BEN           | Request renewal immediately           |
| TIN format invalid       | Verify format matches IRS specs       |
| Name mismatch            | Request corrected form                |

---

## 7. Escalation Contacts

| Role              | Contact              | Escalation Path     |
|-------------------|----------------------|---------------------|
| Primary Owner     | finance@example.com  | Direct              |
| Stripe Support    | support@stripe.com   | After 24h delay     |
| Tax Compliance    | tax@example.com      | Form issues         |
| Security Team     | security@example.com | Credential issues   |

---

## 8. Revision History

| Date       | Author      | Changes                    |
|------------|-------------|----------------------------|
| 2025-01-15 | Finance Ops | Initial runbook creation   |