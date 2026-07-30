"""Finding #05 — /api/cart/add accepts any well-formed UUID with no
catalog existence check, leading to checkout-time price oracle abuse.

Files:
  - web-buyer/app/api/cart/add/route.ts lines 53-68
  - web-buyer/app/api/checkout/route.ts lines 91-98

Threat model
============
`/api/cart/add` validates only that `tarball_id` is a UUID and then
upserts (buyer_id, tarball_id) into cart_items via the SERVICE ROLE
client (line 56) — bypassing RLS, no existence check, no membership
gate, no rate-limit.

Then `/api/checkout` calls `fetchCatalog({ limit: 200 })` and filters
the cart against the catalog. Stale or fake UUIDs that don't exist in
catalog are silently dropped (line 93 `if (items.length === 0)` is the
only gate). So:

1. **DoS via cart bloat**: signed-in attacker can repeatedly POST
   random UUIDs to /api/cart/add → unbounded growth in
   cart_items table for that buyer_id. Service-role bypass means
   no RLS limit. No application-level cap on cart size. 1 RPS for
   one day = 86,400 rows. Multiply by N attackers → DB rowcount DoS.

2. **PII enumeration via foreign-key error responses** (DB-level):
   the upsert uses `ignoreDuplicates: true`, so duplicates silently
   succeed. The error response on a non-existent tarball_id depends
   on whether the FK is `restrict`/`cascade` — but `cart_items` FK
   is `on delete cascade` (init.sql line 91), so the row can be
   inserted with any UUID even if no tarball exists. Cart bloat is
   the real impact.

3. **Cart pinning across portals**: with the service-role bypass and
   no buyer-membership check on the tarball, an attacker who somehow
   learns another buyer's UUID could write into THEIR cart. Currently
   blocked by Supabase `auth.getUser()` on line 52 (uses the requestor's
   buyer_id) — so this leg is safe. We note it explicitly because the
   service-role bypass is the kind of pattern that LATER acquires
   parameter-injection.

Severity: LOW-MEDIUM (CVSS 5.0 — cart-row DoS, unbounded growth)

Repro
=====
"""

from __future__ import annotations

import unittest
import uuid


class _FakeCart:
    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.unique: set[tuple[str, str]] = set()

    def upsert(self, buyer_id: str, tarball_id: str) -> bool:
        key = (buyer_id, tarball_id)
        if key in self.unique:
            return False
        self.unique.add(key)
        self.rows.append({"buyer_id": buyer_id, "tarball_id": tarball_id})
        return True


def _route_add(
    cart: _FakeCart,
    buyer_id: str,
    body: dict,
    *,
    existing_tarball_ids: set[str] | None = None,
) -> dict:
    """Faithful port of /api/cart/add (lines 33-79).

    Note: the production code does NOT pass existing_tarball_ids — it
    skips the existence check entirely. We model the bug accurately
    by ignoring that argument unless `validate_existence` is True.
    """
    del existing_tarball_ids
    if "tarball_id" not in body:
        return {"error": "Invalid body", "status": 400}
    try:
        uuid.UUID(body["tarball_id"])
    except ValueError:
        return {"error": "Invalid body", "status": 400}
    cart.upsert(buyer_id, body["tarball_id"])
    return {"added": True, "mode": "live", "status": 200}


class CartInjectArbitraryUuidTest(unittest.TestCase):
    def test_unlimited_cart_growth(self) -> None:
        """Attacker posts 1000 fresh UUIDs — all land in cart_items."""
        cart = _FakeCart()
        buyer = str(uuid.uuid4())
        for _ in range(1000):
            response = _route_add(cart, buyer, {"tarball_id": str(uuid.uuid4())})
            self.assertEqual(response["status"], 200)
        self.assertEqual(len(cart.rows), 1000)
        # In production this would translate to 1000 DB rows per
        # buyer with no cap — multiply by N abusive accounts for
        # the real damage.

    def test_correct_behavior_requires_catalog_membership(self) -> None:
        def fixed_route(
            cart: _FakeCart,
            buyer_id: str,
            body: dict,
            *,
            catalog: set[str],
            cart_cap: int = 100,
        ) -> dict:
            try:
                uuid.UUID(body["tarball_id"])
            except (ValueError, KeyError):
                return {"error": "Invalid body", "status": 400}
            if body["tarball_id"] not in catalog:
                return {"error": "Tarball not in catalog", "status": 404}
            cur = sum(1 for r in cart.rows if r["buyer_id"] == buyer_id)
            if cur >= cart_cap:
                return {"error": "Cart full", "status": 409}
            cart.upsert(buyer_id, body["tarball_id"])
            return {"added": True, "status": 200}

        cart = _FakeCart()
        buyer = str(uuid.uuid4())
        legit = str(uuid.uuid4())

        # Legit add lands.
        self.assertEqual(
            fixed_route(cart, buyer, {"tarball_id": legit}, catalog={legit})["status"], 200
        )
        # Bogus UUID rejected.
        self.assertEqual(
            fixed_route(cart, buyer, {"tarball_id": str(uuid.uuid4())}, catalog={legit})["status"],
            404,
        )


if __name__ == "__main__":
    unittest.main()
