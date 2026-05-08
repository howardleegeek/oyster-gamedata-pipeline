/**
 * Minimal Stripe Connect client — server-side only.
 *
 * Why hand-rolled instead of `stripe` npm pkg?
 *   1. No npm install was authorised for this change.
 *   2. The surface we touch is small (5 endpoints) and stable.
 *   3. The DEV MODE fallback needs to be a first-class peer of the live
 *      client, which is awkward to do cleanly when the SDK is in the
 *      middle. By owning the seam we keep the mock and the live client
 *      shape-identical.
 *
 * Endpoints touched:
 *   - POST /v1/accounts                (create Express account)
 *   - GET  /v1/accounts/{id}           (read charges_enabled etc.)
 *   - POST /v1/account_links           (onboarding redirect URL)
 *   - POST /v1/login_links             (Express dashboard login URL)
 *   - POST /v1/transfers               (move money on payout day)
 *
 * IRON-LAW: real Stripe API in production. The `mock-by-default` behaviour
 * only kicks in when STRIPE_SECRET_KEY is absent (i.e. local/dev). A
 * production deploy without the env var will surface a [DEV MODE] banner
 * on /payouts so the operator notices immediately.
 */

import { env, isStripeConfigured } from './env';

const STRIPE_API_BASE = 'https://api.stripe.com/v1';

// ---------- Shape definitions (subset of Stripe's API objects) ----------
//
// We intentionally type only the fields we read. Unknown fields are kept
// in `[k: string]: unknown` so future extensions don't require type changes.

export interface StripeAccount {
  id: string;
  object: 'account';
  charges_enabled: boolean;
  payouts_enabled: boolean;
  details_submitted: boolean;
  type: 'express' | 'standard' | 'custom';
  country: string;
  email: string | null;
  created: number;
  capabilities?: Record<string, string>;
  requirements?: {
    currently_due?: string[];
    past_due?: string[];
    disabled_reason?: string | null;
  };
  [k: string]: unknown;
}

export interface StripeAccountLink {
  object: 'account_link';
  url: string;
  expires_at: number;
  created: number;
  [k: string]: unknown;
}

export interface StripeLoginLink {
  object: 'login_link';
  url: string;
  created: number;
  [k: string]: unknown;
}

export interface StripeTransfer {
  id: string;
  object: 'transfer';
  amount: number; // cents
  currency: string; // 'usd'
  destination: string; // acct_xxx
  created: number;
  description: string | null;
  metadata: Record<string, string>;
  [k: string]: unknown;
}

export interface StripePayout {
  id: string;
  object: 'payout';
  amount: number;
  arrival_date: number;
  status: 'pending' | 'paid' | 'failed' | 'in_transit' | 'canceled';
  created: number;
  [k: string]: unknown;
}

export interface StripeError {
  type: string;
  code?: string;
  message: string;
  param?: string;
}

export class StripeApiError extends Error {
  readonly status: number;
  readonly stripeError: StripeError;

  constructor(status: number, stripeError: StripeError) {
    super(`Stripe ${status}: ${stripeError.message}`);
    this.status = status;
    this.stripeError = stripeError;
  }
}

// ---------- Client interface ----------

export interface StripeClient {
  /** True when this client talks to real Stripe; false for the in-process mock. */
  readonly live: boolean;

  createExpressAccount(params: {
    email: string;
    metadata?: Record<string, string>;
  }): Promise<StripeAccount>;

  retrieveAccount(accountId: string): Promise<StripeAccount>;

  createAccountLink(params: {
    accountId: string;
    refreshUrl: string;
    returnUrl: string;
    type?: 'account_onboarding' | 'account_update';
  }): Promise<StripeAccountLink>;

  createLoginLink(accountId: string): Promise<StripeLoginLink>;

  createTransfer(params: {
    amountCents: number;
    destinationAccountId: string;
    idempotencyKey: string;
    description?: string;
    metadata?: Record<string, string>;
  }): Promise<StripeTransfer>;

  listPayouts(params: { accountId: string; limit?: number }): Promise<{
    object: 'list';
    data: StripePayout[];
    has_more: boolean;
  }>;
}

// =====================================================================
// LIVE client — wraps fetch() against api.stripe.com
// =====================================================================

class LiveStripeClient implements StripeClient {
  readonly live = true as const;

  constructor(private readonly secretKey: string) {
    if (!secretKey.startsWith('sk_')) {
      throw new Error('LiveStripeClient requires a secret key starting with sk_');
    }
  }

  private async request<T>(
    method: 'GET' | 'POST',
    path: string,
    options: {
      body?: Record<string, unknown>;
      idempotencyKey?: string;
      stripeAccount?: string;
    } = {}
  ): Promise<T> {
    const headers: Record<string, string> = {
      Authorization: `Bearer ${this.secretKey}`,
      'Content-Type': 'application/x-www-form-urlencoded',
    };
    if (options.idempotencyKey) headers['Idempotency-Key'] = options.idempotencyKey;
    if (options.stripeAccount) headers['Stripe-Account'] = options.stripeAccount;

    const init: RequestInit = { method, headers };
    if (options.body) init.body = encodeForm(options.body);

    const res = await fetch(`${STRIPE_API_BASE}${path}`, init);
    const json = (await res.json()) as { error?: StripeError } & T;
    if (!res.ok) {
      throw new StripeApiError(
        res.status,
        json.error ?? { type: 'api_error', message: `HTTP ${res.status}` }
      );
    }
    return json;
  }

  async createExpressAccount(params: {
    email: string;
    metadata?: Record<string, string>;
  }): Promise<StripeAccount> {
    const body: Record<string, unknown> = {
      type: 'express',
      country: 'US',
      email: params.email,
      'capabilities[transfers][requested]': 'true',
    };
    for (const [k, v] of Object.entries(params.metadata ?? {})) {
      body[`metadata[${k}]`] = v;
    }
    return this.request<StripeAccount>('POST', '/accounts', { body });
  }

  async retrieveAccount(accountId: string): Promise<StripeAccount> {
    return this.request<StripeAccount>('GET', `/accounts/${encodeURIComponent(accountId)}`);
  }

  async createAccountLink(params: {
    accountId: string;
    refreshUrl: string;
    returnUrl: string;
    type?: 'account_onboarding' | 'account_update';
  }): Promise<StripeAccountLink> {
    return this.request<StripeAccountLink>('POST', '/account_links', {
      body: {
        account: params.accountId,
        refresh_url: params.refreshUrl,
        return_url: params.returnUrl,
        type: params.type ?? 'account_onboarding',
      },
    });
  }

  async createLoginLink(accountId: string): Promise<StripeLoginLink> {
    return this.request<StripeLoginLink>(
      'POST',
      `/accounts/${encodeURIComponent(accountId)}/login_links`,
      { body: {} }
    );
  }

  async createTransfer(params: {
    amountCents: number;
    destinationAccountId: string;
    idempotencyKey: string;
    description?: string;
    metadata?: Record<string, string>;
  }): Promise<StripeTransfer> {
    const body: Record<string, unknown> = {
      amount: params.amountCents,
      currency: 'usd',
      destination: params.destinationAccountId,
    };
    if (params.description) body.description = params.description;
    for (const [k, v] of Object.entries(params.metadata ?? {})) {
      body[`metadata[${k}]`] = v;
    }
    return this.request<StripeTransfer>('POST', '/transfers', {
      body,
      idempotencyKey: params.idempotencyKey,
    });
  }

  async listPayouts(params: {
    accountId: string;
    limit?: number;
  }): Promise<{ object: 'list'; data: StripePayout[]; has_more: boolean }> {
    const limit = params.limit ?? 10;
    return this.request('GET', `/payouts?limit=${limit}`, {
      stripeAccount: params.accountId,
    });
  }
}

// Helper — Stripe expects application/x-www-form-urlencoded with bracket
// notation for nested objects/arrays. We only need flat key/value here
// because callers pre-flatten via `metadata[foo]` etc.
function encodeForm(body: Record<string, unknown>): string {
  const parts: string[] = [];
  for (const [key, value] of Object.entries(body)) {
    if (value === undefined || value === null) continue;
    parts.push(`${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`);
  }
  return parts.join('&');
}

// =====================================================================
// MOCK client — synthetic IDs, real Stripe-shaped objects
// =====================================================================
//
// Behaviour rules (matter for the test suite):
//   1. createExpressAccount() returns a fresh acct_id; charges_enabled=false.
//   2. retrieveAccount() returns whatever was stored; the mock auto-flips
//      charges_enabled=true after the first createAccountLink() call so the
//      "return from onboarding" path can be exercised end-to-end.
//   3. createTransfer() with the same idempotencyKey returns the SAME
//      object (no duplicate), matching real Stripe semantics.

interface MockAccountState {
  account: StripeAccount;
  onboardingStarted: boolean;
}

class MockStripeClient implements StripeClient {
  readonly live = false as const;

  // Module-singleton-style storage so multiple route handlers in the same
  // process see a coherent view. Cleared by `__resetMockStripeStateForTests()`.
  private static accounts = new Map<string, MockAccountState>();
  private static transfers = new Map<string, StripeTransfer>(); // keyed by idempotency key
  private static counter = 1;

  private nextId(prefix: string): string {
    const tail = (Date.now().toString(36) + (MockStripeClient.counter++).toString(36)).slice(-16);
    return `${prefix}_mock_${tail}`;
  }

  async createExpressAccount(params: {
    email: string;
    metadata?: Record<string, string>;
  }): Promise<StripeAccount> {
    const id = this.nextId('acct');
    const account: StripeAccount = {
      id,
      object: 'account',
      type: 'express',
      country: 'US',
      email: params.email,
      created: Math.floor(Date.now() / 1000),
      charges_enabled: false,
      payouts_enabled: false,
      details_submitted: false,
      capabilities: { transfers: 'inactive' },
      requirements: {
        currently_due: ['external_account', 'tos_acceptance.date', 'tos_acceptance.ip'],
        past_due: [],
        disabled_reason: 'requirements.past_due',
      },
    };
    MockStripeClient.accounts.set(id, { account, onboardingStarted: false });
    return account;
  }

  async retrieveAccount(accountId: string): Promise<StripeAccount> {
    const state = MockStripeClient.accounts.get(accountId);
    if (!state) {
      throw new StripeApiError(404, {
        type: 'invalid_request_error',
        code: 'account_invalid',
        message: `No such account: '${accountId}' (mock)`,
      });
    }
    return { ...state.account };
  }

  async createAccountLink(params: {
    accountId: string;
    refreshUrl: string;
    returnUrl: string;
    type?: 'account_onboarding' | 'account_update';
  }): Promise<StripeAccountLink> {
    const state = MockStripeClient.accounts.get(params.accountId);
    if (!state) {
      throw new StripeApiError(404, {
        type: 'invalid_request_error',
        code: 'account_invalid',
        message: `No such account: '${params.accountId}' (mock)`,
      });
    }
    // First time the user hits the onboarding link, simulate a completed flow
    // on the next retrieveAccount() so /api/stripe/connect/return can verify.
    if (!state.onboardingStarted) {
      state.onboardingStarted = true;
      state.account = {
        ...state.account,
        charges_enabled: true,
        payouts_enabled: true,
        details_submitted: true,
        capabilities: { transfers: 'active' },
        requirements: { currently_due: [], past_due: [], disabled_reason: null },
      };
    }
    return {
      object: 'account_link',
      url: `https://connect.stripe.com/express/onboarding/mock/${encodeURIComponent(params.accountId)}`,
      expires_at: Math.floor(Date.now() / 1000) + 5 * 60,
      created: Math.floor(Date.now() / 1000),
    };
  }

  async createLoginLink(accountId: string): Promise<StripeLoginLink> {
    if (!MockStripeClient.accounts.has(accountId)) {
      throw new StripeApiError(404, {
        type: 'invalid_request_error',
        code: 'account_invalid',
        message: `No such account: '${accountId}' (mock)`,
      });
    }
    return {
      object: 'login_link',
      url: `https://connect.stripe.com/express/dashboard/mock/${encodeURIComponent(accountId)}`,
      created: Math.floor(Date.now() / 1000),
    };
  }

  async createTransfer(params: {
    amountCents: number;
    destinationAccountId: string;
    idempotencyKey: string;
    description?: string;
    metadata?: Record<string, string>;
  }): Promise<StripeTransfer> {
    // Idempotency: same key → same object, never a duplicate.
    const existing = MockStripeClient.transfers.get(params.idempotencyKey);
    if (existing) return existing;

    const id = this.nextId('tr');
    const transfer: StripeTransfer = {
      id,
      object: 'transfer',
      amount: params.amountCents,
      currency: 'usd',
      destination: params.destinationAccountId,
      created: Math.floor(Date.now() / 1000),
      description: params.description ?? null,
      metadata: params.metadata ?? {},
    };
    MockStripeClient.transfers.set(params.idempotencyKey, transfer);
    return transfer;
  }

  async listPayouts(params: { accountId: string; limit?: number }): Promise<{
    object: 'list';
    data: StripePayout[];
    has_more: boolean;
  }> {
    // Synthesize one in-flight + a couple of paid payouts so the UI shows
    // realistic state without needing a real Stripe history.
    void params;
    const now = Math.floor(Date.now() / 1000);
    return {
      object: 'list',
      has_more: false,
      data: [
        {
          id: this.nextId('po'),
          object: 'payout',
          amount: 4200,
          arrival_date: now + 2 * 86400,
          status: 'in_transit',
          created: now - 3 * 3600,
        },
        {
          id: this.nextId('po'),
          object: 'payout',
          amount: 6300,
          arrival_date: now - 5 * 86400,
          status: 'paid',
          created: now - 8 * 86400,
        },
      ],
    };
  }
}

/**
 * Test-only: reset the in-process mock state. Imported by
 * `tests/test_stripe_connect.py`-driven JS tests if/when we add them.
 */
export function __resetMockStripeStateForTests(): void {
  // @ts-expect-error reaching into private static for test reset
  MockStripeClient.accounts = new Map();
  // @ts-expect-error idem
  MockStripeClient.transfers = new Map();
  // @ts-expect-error idem
  MockStripeClient.counter = 1;
}

// =====================================================================
// Factory
// =====================================================================

let _client: StripeClient | null = null;

/**
 * Howard 2026-05-07 IRON-LAW: getStripeClient() returns a LIVE client
 * only. If STRIPE_SECRET_KEY is missing the function THROWS — callers
 * MUST guard with `isStripeConfigured()` and render <NotConfigured>.
 *
 * The previous silent fallback to MockStripeClient was a placeholder-
 * by-another-name: the page rendered fake `acct_mock_*` IDs in the UI
 * without flagging that the data wasn't real. Now: explicit failure.
 *
 * MockStripeClient lives on for *tests only* (imported directly by the
 * test file via `__resetMockStripeStateForTests` + manual instantiation).
 */
export function getStripeClient(): StripeClient {
  if (_client) return _client;
  if (!isStripeConfigured()) {
    throw new Error(
      'Stripe not configured. Set STRIPE_SECRET_KEY before calling ' +
        'getStripeClient(). Page-level callers MUST guard with ' +
        'isStripeConfigured() and render <NotConfigured> instead.',
    );
  }
  _client = new LiveStripeClient(env.stripeSecretKey);
  return _client;
}

/**
 * TEST-ONLY: explicit constructor for the mock client. NOT exported
 * from the route handler path — only `tests/test_stripe_connect.py`
 * (or future TS test files) should reach for this.
 */
export function __testOnlyMockClient(): StripeClient {
  return new MockStripeClient();
}

/** Test-only: drop the cached client so the next call picks up new env. */
export function __resetStripeClientForTests(): void {
  _client = null;
}
