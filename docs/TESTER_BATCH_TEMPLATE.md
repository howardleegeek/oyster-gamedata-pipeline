# Tester Batch Invite Template

This template is used by `bin/send_batch_invites.py` to generate
ready-to-copy email bodies for newly approved beta testers.

## Placeholders

| Placeholder    | Source                                    |
|----------------|-------------------------------------------|
| `{name}`       | Derived from email prefix (before `@`)    |
| `{download_url}`| Returned by `POST /api/v1/testers/{id}/approve` |
| `{tester_id}`  | Returned by `POST /api/v1/testers/apply`  |

## Email Body

```
Subject: You're in! Beta access for gamedata-pipeline

Hi {name},

Great news — your beta application has been approved!

Quick install link: {download_url}
Your tester ID: {tester_id}

Next steps:
• Download the installer using the link above
• Run the installer and follow the on-screen prompts
• Launch the app and verify you see the beta badge

If you run into any issues, reach out on our Discord support channel:
  https://discord.gg/gamedata-pipeline

Disclaimer: This is alpha software. Expect bugs, missing features,
and occasional crashes. Your feedback is invaluable — please report
any issues on Discord or via the in-app bug reporter.

Cheers,
The gamedata-pipeline team
```

## Usage

```bash
OYSTER_ADMIN_TOKEN=secret python3 bin/send_batch_invites.py \
  --emails howard@x.com,bruno@y.com,foo@z.com \
  --backend http://localhost:8500
```

- Max **10 emails** per batch (rate-limit safety).
- Output goes to **stdout** — no SMTP, no file writes.
- Howard copies the output and sends manually.
