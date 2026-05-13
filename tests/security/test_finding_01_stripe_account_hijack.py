"""Finding #01 — Stripe Connect account hijack via `?account=` query param.

File: web-tester/app/api/stripe/connect/return/route.ts (lines 38, 58, 63-71)

Threat model
============
The Stripe Connect "return" endpoint accepts an OPTIONAL `?account=acct_xxx`
query parameter. When present, it overrides `tester.stripe_account_id` from
the DB. The route then calls `stripe.retrieveAccount(account)` and writes
`account.id` back to the tester row.

Result: if an attacker can lure a signed-in tester to GET
`/api/stripe/connect/return?account=<attacker_acct_id>`, the victim's
payout destination is silently replaced with the attacker's account.

Then payout_cron.py picks `stripe_account_id` from `tester_unpaid_balance`
and creates a Stripe Transfer to the attacker.

Severity: CRITICAL (CVSS 9.6 — funds theft, one-click)

Repro (logic, no live HTTP)
===========================
Simulate the route's update-path with `account` from the query string and
demonstrate the DB write would replace the destination.
"""
from __future__ import annotations

import unittest


class _FakeTesters:
    """In-memory analogue of the testers table."""

    def __init__(self, rows: dict[str, dict]) -> None:
        self.rows = rows
        self.updates: list[tuple[str, dict]] = []

    def update(self, tester_id: str, fields: dict) -> None:
        self.rows[tester_id].update(fields)
        self.updates.append((tester_id, fields))


def _route_return_logic(
    *,
    user_id: str,
    testers: _FakeTesters,
    account_from_query: str | None,
    stripe_retrieve,
) -> dict:
    """Faithful Python port of web-tester/app/api/stripe/connect/return/route.ts."""
    tester = testers.rows.get(user_id)
    account_id = account_from_query or (tester or {}).get("stripe_account_id")
    if not account_id:
        return {"error": "no_account"}
    # vulnerability: stripe.retrieveAccount() succeeds for the *attacker's*
    # public Stripe account ID and returns the attacker's account.
    acct = stripe_retrieve(account_id)
    # vulnerability: route writes account.id (whatever Stripe returned)
    # back to the *victim's* tester row.
    testers.update(
        user_id,
        {
            "stripe_account_id": acct["id"],
            "stripe_charges_enabled": acct["charges_enabled"],
            "stripe_payouts_enabled": acct["payouts_enabled"],
        },
    )
    return {"ok": True, "account_id": acct["id"]}


class StripeAccountHijackTest(unittest.TestCase):
    def test_query_param_overrides_db_account(self) -> None:
        """Attacker sends victim a link with ?account=acct_attacker.

        Victim is signed in (CSRF-style — GET on a logged-in browser).
        The endpoint must NOT replace the victim's stripe_account_id with
        a value supplied by the URL.
        """
        testers = _FakeTesters(
            {
                "victim-uuid": {
                    "id": "victim-uuid",
                    "stripe_account_id": "acct_victim_legit",
                },
            }
        )

        def stripe_retrieve(acct_id: str) -> dict:
            # Real Stripe will happily return *any* account the platform
            # has access to via /v1/accounts/{id} — including accounts
            # the attacker controls (a different connected account on
            # the same platform).
            return {
                "id": acct_id,
                "charges_enabled": True,
                "payouts_enabled": True,
                "details_submitted": True,
            }

        # Exploit: attacker crafts a link with their own connected account ID.
        _route_return_logic(
            user_id="victim-uuid",
            testers=testers,
            account_from_query="acct_attacker_steal",
            stripe_retrieve=stripe_retrieve,
        )

        # Bug: victim's row now points to attacker's account.
        self.assertEqual(
            testers.rows["victim-uuid"]["stripe_account_id"],
            "acct_attacker_steal",
            "Expected victim's stripe_account_id to be hijacked by the "
            "query-string parameter — confirming the vuln.",
        )

    def test_correct_behavior_should_ignore_query_param(self) -> None:
        """Regression test that should pass after the fix.

        Fix: drop `accountIdFromQuery` entirely. The endpoint MUST source
        stripe_account_id ONLY from the testers table (the DB write the
        onboarding step set). Stripe does not require it in the return URL.
        """

        # After fix, the route would look like:
        def fixed_route(
            user_id: str,
            testers: _FakeTesters,
            account_from_query: str | None,
            stripe_retrieve,
        ) -> dict:
            del account_from_query  # ignored — fix
            tester = testers.rows.get(user_id)
            account_id = (tester or {}).get("stripe_account_id")
            if not account_id:
                return {"error": "no_account"}
            acct = stripe_retrieve(account_id)
            testers.update(
                user_id,
                {
                    "stripe_charges_enabled": acct["charges_enabled"],
                    "stripe_payouts_enabled": acct["payouts_enabled"],
                },
            )
            return {"ok": True, "account_id": acct["id"]}

        testers = _FakeTesters(
            {
                "victim-uuid": {
                    "id": "victim-uuid",
                    "stripe_account_id": "acct_victim_legit",
                },
            }
        )
        fixed_route(
            user_id="victim-uuid",
            testers=testers,
            account_from_query="acct_attacker_steal",
            stripe_retrieve=lambda a: {
                "id": a,
                "charges_enabled": True,
                "payouts_enabled": True,
                "details_submitted": True,
            },
        )
        self.assertEqual(
            testers.rows["victim-uuid"]["stripe_account_id"], "acct_victim_legit"
        )


if __name__ == "__main__":
    unittest.main()
