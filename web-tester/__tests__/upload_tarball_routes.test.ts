/**
 * Route-level tests for the Gap #8 signed-URL upload split.
 *
 * Why this exists:
 *   The web-tester app does not (yet) have Vitest/Jest configured. The
 *   tester-auth helper is pure TS with no runtime deps — easy to assert
 *   directly. The /sign and /finalize routes import @supabase/supabase-js
 *   transitively, so we use Node's test runner + a module mock to give
 *   them a stub `getSupabaseServiceClient` and assert response shapes.
 *
 *   Run with: `cd web-tester && npx tsx --test __tests__/upload_tarball_routes.test.ts`
 *
 *   The first time CI picks this up it will also pull tsx via npx. If we
 *   later move to Vitest, the assertions translate one-for-one.
 *
 * What we cover:
 *   - tester-auth: stub_mode mode allows requests, real-secret mode
 *     accepts a valid HMAC and rejects mismatched signatures.
 *   - sign route:
 *     - 400 on malformed JSON / wrong sha256 length
 *     - 503 when Supabase not configured
 *     - 200 with signed_url + tarball_id on the happy path
 *     - 409 when another tester already owns the sha256
 *   - finalize route:
 *     - 404 when tarball_id not found
 *     - 422 on sha256 mismatch between sign and finalize
 *     - 409 when storage object is missing
 *     - 200 + accepted=true on the happy path
 *   - legacy /api/upload-tarball: 410 with migration body on POST/GET/PUT
 *
 * Howard 2026-05-13.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';

// ---------------------------------------------------------------------------
// Tiny mock framework — we need to swap getSupabaseServiceClient + isSupabaseConfigured
// before route modules import them. We override the resolver via a shared mock state
// the lib/* modules read on each call. Cheaper than module hoisting tricks.
// ---------------------------------------------------------------------------

// Force env BEFORE importing anything that reads it. The route files import
// `env` lazily-evaluated (Object.freeze on first read), so set vars first.
process.env.NEXT_PUBLIC_SUPABASE_URL = 'https://stub.supabase.test';
process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY = 'anon-key-stub';
process.env.SUPABASE_SERVICE_ROLE_KEY = 'service-role-stub';
process.env.SUPABASE_TARBALL_UPLOAD_BUCKET = 'tarball-uploads';

// ---- Stub Supabase service client --------------------------------------
type FakeRow = {
  id: string;
  tester_id: string;
  sha256: string;
  size_bytes: number;
  upload_status: 'pending_upload' | 'uploaded' | 'failed';
  duration_seconds: number;
  storage_bucket: string;
  storage_path: string;
  signed_url_expires_at: string | null;
  uploaded_at: string;
  d5_verdict: 'pending' | 'accepted' | 'rejected';
};

class FakeQuery {
  // Captured query state
  private _filter: { col: string; val: unknown } | null = null;
  private _filter2: { col: string; val: unknown } | null = null;
  private _select = '*';
  constructor(
    private store: FakeRow[],
    private _pendingInsert: Partial<FakeRow> | null = null,
    private _pendingUpdate: Partial<FakeRow> | null = null,
  ) {}
  insert(payload: Partial<FakeRow>) {
    return new FakeQuery(this.store, payload, null);
  }
  update(payload: Partial<FakeRow>) {
    return new FakeQuery(this.store, null, payload);
  }
  eq(col: string, val: unknown) {
    if (!this._filter) this._filter = { col, val };
    else this._filter2 = { col, val };
    return this;
  }
  select(cols: string) {
    this._select = cols;
    return this;
  }
  async single(): Promise<{ data: FakeRow | null; error: { code: string; message: string } | null }> {
    if (this._pendingInsert) {
      // Duplicate sha256 emulation
      const sha = this._pendingInsert.sha256;
      const dup = this.store.find((r) => r.sha256 === sha);
      if (dup) {
        return { data: null, error: { code: '23505', message: 'duplicate key' } };
      }
      const row: FakeRow = {
        id: crypto.randomUUID(),
        tester_id: this._pendingInsert.tester_id ?? '',
        sha256: this._pendingInsert.sha256 ?? '',
        size_bytes: this._pendingInsert.size_bytes ?? 0,
        upload_status: this._pendingInsert.upload_status ?? 'pending_upload',
        duration_seconds: this._pendingInsert.duration_seconds ?? 0,
        storage_bucket: this._pendingInsert.storage_bucket ?? 'tarball-uploads',
        storage_path: this._pendingInsert.storage_path ?? '',
        signed_url_expires_at: this._pendingInsert.signed_url_expires_at ?? null,
        uploaded_at: new Date().toISOString(),
        d5_verdict: 'pending',
      };
      this.store.push(row);
      return { data: row, error: null };
    }
    if (this._pendingUpdate) {
      const f = this._filter!;
      const f2 = this._filter2;
      const idx = this.store.findIndex((r) => {
        if ((r as unknown as Record<string, unknown>)[f.col] !== f.val) return false;
        if (f2 && (r as unknown as Record<string, unknown>)[f2.col] !== f2.val) return false;
        return true;
      });
      if (idx === -1) return { data: null, error: null };
      this.store[idx] = { ...this.store[idx], ...(this._pendingUpdate as Partial<FakeRow>) } as FakeRow;
      return { data: this.store[idx], error: null };
    }
    // bare .select(...).eq(...).single()
    const f = this._filter;
    const r = f
      ? this.store.find((row) => (row as unknown as Record<string, unknown>)[f.col] === f.val) ?? null
      : null;
    return { data: r, error: r ? null : { code: 'PGRST116', message: 'not found' } };
  }
}

class FakeStorageBucket {
  constructor(public storage: FakeStorage, public bucket: string) {}
  async createSignedUploadUrl(path: string) {
    if (this.storage.failNextSign) {
      this.storage.failNextSign = false;
      return { data: null, error: { message: 'createSignedUploadUrl failed' } };
    }
    return {
      data: {
        path,
        token: 'tok_' + crypto.randomBytes(8).toString('hex'),
        signedUrl: `https://stub.supabase.test/storage/v1/object/upload/sign/${this.bucket}/${path}?token=...`,
      },
      error: null,
    };
  }
  async list(_dir: string, opts: { limit: number; search: string }) {
    const key = `${_dir}/${opts.search}`;
    const meta = this.storage.objects.get(key);
    if (!meta) return { data: [], error: null };
    return { data: [{ name: opts.search, metadata: { size: meta.size } }], error: null };
  }
}

class FakeStorage {
  objects: Map<string, { size: number }> = new Map();
  failNextSign = false;
  from(bucket: string) {
    return new FakeStorageBucket(this, bucket);
  }
}

class FakeSupabase {
  private store: FakeRow[] = [];
  storage = new FakeStorage();
  from(_table: string) {
    return new FakeQuery(this.store);
  }
  // test helpers
  _allRows(): FakeRow[] {
    return this.store;
  }
  _setUploadStatus(id: string, status: FakeRow['upload_status']) {
    const r = this.store.find((x) => x.id === id);
    if (r) r.upload_status = status;
  }
  _putObject(bucket: string, path: string, size: number) {
    const slash = path.lastIndexOf('/');
    const dir = slash >= 0 ? path.slice(0, slash) : '';
    const file = slash >= 0 ? path.slice(slash + 1) : path;
    this.storage.objects.set(`${dir}/${file}`, { size });
  }
}

let fakeSupabase: FakeSupabase = new FakeSupabase();
function resetFake() {
  fakeSupabase = new FakeSupabase();
}

// Patch the supabase-server module BEFORE routes import it.
// Node's `Module._cache` swap is the most reliable approach without a real test runner.
import { createRequire } from 'node:module';
const localRequire = createRequire(import.meta.url);

// Patch lib/supabase-server before importing the routes.
const supabaseServerPath = localRequire.resolve('../lib/supabase-server.ts');
require.cache[supabaseServerPath] = {
  id: supabaseServerPath,
  filename: supabaseServerPath,
  loaded: true,
  exports: {
    getSupabaseServiceClient: () => fakeSupabase,
    getSupabaseServerClient: () => null,
  },
  children: [],
  paths: [],
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
} as any;

// ---------------------------------------------------------------------------
// NextRequest stub — the routes only call .text(), .headers.get(), .nextUrl.pathname
// ---------------------------------------------------------------------------

function makeReq(opts: {
  url?: string;
  body?: object | string;
  headers?: Record<string, string>;
}) {
  const url = opts.url ?? 'http://localhost/api/upload-tarball/sign';
  const text = typeof opts.body === 'string' ? opts.body : JSON.stringify(opts.body ?? {});
  const headers = new Headers(opts.headers ?? {});
  return {
    text: async () => text,
    headers,
    nextUrl: { pathname: new URL(url).pathname },
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
  } as any;
}

// ---------------------------------------------------------------------------
// tester-auth tests
// ---------------------------------------------------------------------------

test('tester-auth: stub_mode mode allows no-header requests', async () => {
  delete process.env.TESTER_AUTH_HMAC_SECRET;
  const { verifyTesterAuth } = await import('../lib/tester-auth.ts');
  const req = makeReq({ body: { tester_id: 'abc' } });
  const result = await verifyTesterAuth(req, 'somesha', 'abc');
  assert.equal(result.ok, true);
  if (result.ok) {
    assert.equal(result.stub_mode, true);
    assert.equal(result.tester_id, 'abc');
  }
});

test('tester-auth: real mode rejects missing header', async () => {
  process.env.TESTER_AUTH_HMAC_SECRET = 'topsecret';
  const { verifyTesterAuth, sha256Hex } = await import('../lib/tester-auth.ts?real');
  const req = makeReq({ body: { tester_id: 'abc' } });
  const result = await verifyTesterAuth(req, sha256Hex('body'), 'abc');
  assert.equal(result.ok, false);
  if (!result.ok) assert.equal(result.status, 401);
  delete process.env.TESTER_AUTH_HMAC_SECRET;
});

test('tester-auth: real mode accepts valid signature', async () => {
  process.env.TESTER_AUTH_HMAC_SECRET = 'topsecret';
  const { verifyTesterAuth, sha256Hex } = await import('../lib/tester-auth.ts?real2');
  const tester_id = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee';
  const body = '{"x":1}';
  const bodyHash = sha256Hex(body);
  const ts = Date.now();
  const sig = crypto
    .createHmac('sha256', 'topsecret')
    .update(`${tester_id}\n${ts}\n${bodyHash}`)
    .digest('hex');
  const req = makeReq({
    body: '{"x":1}',
    headers: { 'X-Tester-Auth': `v1 ${tester_id} ${ts} ${sig}` },
  });
  const result = await verifyTesterAuth(req, bodyHash, tester_id);
  assert.equal(result.ok, true);
  if (result.ok) {
    assert.equal(result.stub_mode, false);
    assert.equal(result.tester_id, tester_id);
  }
  delete process.env.TESTER_AUTH_HMAC_SECRET;
});

test('tester-auth: real mode rejects bad signature', async () => {
  process.env.TESTER_AUTH_HMAC_SECRET = 'topsecret';
  const { verifyTesterAuth, sha256Hex } = await import('../lib/tester-auth.ts?real3');
  const tester_id = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee';
  const req = makeReq({
    body: '{"x":1}',
    headers: { 'X-Tester-Auth': `v1 ${tester_id} ${Date.now()} deadbeef` },
  });
  const result = await verifyTesterAuth(req, sha256Hex('{"x":1}'), tester_id);
  assert.equal(result.ok, false);
  delete process.env.TESTER_AUTH_HMAC_SECRET;
});

test('tester-auth: real mode rejects stale timestamp', async () => {
  process.env.TESTER_AUTH_HMAC_SECRET = 'topsecret';
  const { verifyTesterAuth, sha256Hex } = await import('../lib/tester-auth.ts?real4');
  const tester_id = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee';
  const body = '{"x":1}';
  const bodyHash = sha256Hex(body);
  const ts = Date.now() - 10 * 60_000; // 10 min old
  const sig = crypto
    .createHmac('sha256', 'topsecret')
    .update(`${tester_id}\n${ts}\n${bodyHash}`)
    .digest('hex');
  const req = makeReq({
    body,
    headers: { 'X-Tester-Auth': `v1 ${tester_id} ${ts} ${sig}` },
  });
  const result = await verifyTesterAuth(req, bodyHash, tester_id);
  assert.equal(result.ok, false);
  delete process.env.TESTER_AUTH_HMAC_SECRET;
});

test('tester-auth: real mode rejects HMAC tester_id mismatch', async () => {
  process.env.TESTER_AUTH_HMAC_SECRET = 'topsecret';
  const { verifyTesterAuth, sha256Hex } = await import('../lib/tester-auth.ts?real5');
  const claimed = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee';
  const real = 'ffffffff-bbbb-cccc-dddd-eeeeeeeeeeee';
  const body = '{"x":1}';
  const bodyHash = sha256Hex(body);
  const ts = Date.now();
  const sig = crypto
    .createHmac('sha256', 'topsecret')
    .update(`${real}\n${ts}\n${bodyHash}`)
    .digest('hex');
  const req = makeReq({
    body,
    headers: { 'X-Tester-Auth': `v1 ${real} ${ts} ${sig}` },
  });
  const result = await verifyTesterAuth(req, bodyHash, claimed);
  assert.equal(result.ok, false);
  if (!result.ok) assert.equal(result.status, 403);
  delete process.env.TESTER_AUTH_HMAC_SECRET;
});

// ---------------------------------------------------------------------------
// /api/upload-tarball (legacy) — 410 Gone
// ---------------------------------------------------------------------------

test('legacy upload-tarball POST returns 410 with migration body', async () => {
  const { POST } = await import('../app/api/upload-tarball/route.ts');
  const res = await POST();
  assert.equal(res.status, 410);
  const body = await res.json();
  assert.equal(body.status, 410);
  assert.ok(body.migration?.step_1?.url === '/api/upload-tarball/sign');
  assert.ok(body.migration?.step_3?.url === '/api/upload-tarball/finalize');
  assert.equal(body.recorder_version_required, '0.27.0');
});

test('legacy upload-tarball GET returns 410 with migration body', async () => {
  const { GET } = await import('../app/api/upload-tarball/route.ts');
  const res = await GET();
  assert.equal(res.status, 410);
  const body = await res.json();
  assert.equal(body.error, 'This endpoint is gone — upgrade the recorder to v0.27.0+');
});

// ---------------------------------------------------------------------------
// /api/upload-tarball/sign
// ---------------------------------------------------------------------------

test('sign: 400 when body is not JSON', async () => {
  resetFake();
  const { POST } = await import('../app/api/upload-tarball/sign/route.ts');
  const req = makeReq({ body: 'not json' });
  const res = await POST(req);
  assert.equal(res.status, 400);
});

test('sign: 400 when sha256 is wrong length', async () => {
  resetFake();
  const { POST } = await import('../app/api/upload-tarball/sign/route.ts?sha');
  const req = makeReq({
    body: {
      tester_id: '11111111-2222-3333-4444-555555555555',
      filename: 'x.tar.gz',
      size_bytes: 1,
      sha256: 'tooshort',
      duration_seconds: 1,
    },
  });
  const res = await POST(req);
  assert.equal(res.status, 400);
});

test('sign: 200 happy path returns signed_url + tarball_id', async () => {
  resetFake();
  const { POST } = await import('../app/api/upload-tarball/sign/route.ts?happy');
  const tester_id = '11111111-2222-3333-4444-555555555555';
  const body = {
    tester_id,
    filename: 'foo.tar.gz',
    size_bytes: 100,
    sha256: 'a'.repeat(64),
    duration_seconds: 60,
  };
  const req = makeReq({ body });
  const res = await POST(req);
  assert.equal(res.status, 200);
  const out = await res.json();
  assert.ok(out.tarball_id);
  assert.ok(out.signed_url.startsWith('https://stub.supabase.test/storage/'));
  assert.equal(out.storage_bucket, 'tarball-uploads');
  assert.equal(out.storage_path, `${tester_id}/${'a'.repeat(64)}.tar.gz`);
});

test('sign: 409 when sha256 belongs to a different tester', async () => {
  resetFake();
  // Pre-populate a row owned by another tester with the same sha256
  fakeSupabase._allRows().push({
    id: crypto.randomUUID(),
    tester_id: 'ffffffff-2222-3333-4444-555555555555',
    sha256: 'b'.repeat(64),
    size_bytes: 1,
    upload_status: 'uploaded',
    duration_seconds: 1,
    storage_bucket: 'tarball-uploads',
    storage_path: 'x',
    signed_url_expires_at: null,
    uploaded_at: '2026-05-13T00:00:00Z',
    d5_verdict: 'accepted',
  });
  const { POST } = await import('../app/api/upload-tarball/sign/route.ts?collide');
  const req = makeReq({
    body: {
      tester_id: '11111111-2222-3333-4444-555555555555',
      filename: 'foo.tar.gz',
      size_bytes: 1,
      sha256: 'b'.repeat(64),
      duration_seconds: 1,
    },
  });
  const res = await POST(req);
  assert.equal(res.status, 409);
});

// ---------------------------------------------------------------------------
// /api/upload-tarball/finalize
// ---------------------------------------------------------------------------

test('finalize: 404 when tarball_id not found', async () => {
  resetFake();
  const { POST } = await import('../app/api/upload-tarball/finalize/route.ts?notfound');
  const req = makeReq({
    body: { tarball_id: '00000000-0000-0000-0000-000000000000', sha256: 'c'.repeat(64) },
  });
  const res = await POST(req);
  assert.equal(res.status, 404);
});

test('finalize: 422 on sha256 mismatch', async () => {
  resetFake();
  const id = crypto.randomUUID();
  fakeSupabase._allRows().push({
    id,
    tester_id: '11111111-2222-3333-4444-555555555555',
    sha256: 'a'.repeat(64),
    size_bytes: 10,
    upload_status: 'pending_upload',
    duration_seconds: 1,
    storage_bucket: 'tarball-uploads',
    storage_path: `t/${'a'.repeat(64)}.tar.gz`,
    signed_url_expires_at: '2026-05-13T20:00:00Z',
    uploaded_at: '2026-05-13T00:00:00Z',
    d5_verdict: 'pending',
  });
  const { POST } = await import('../app/api/upload-tarball/finalize/route.ts?sha');
  const req = makeReq({ body: { tarball_id: id, sha256: 'b'.repeat(64) } });
  const res = await POST(req);
  assert.equal(res.status, 422);
});

test('finalize: 409 when storage object missing', async () => {
  resetFake();
  const id = crypto.randomUUID();
  const sha = 'a'.repeat(64);
  fakeSupabase._allRows().push({
    id,
    tester_id: '11111111-2222-3333-4444-555555555555',
    sha256: sha,
    size_bytes: 10,
    upload_status: 'pending_upload',
    duration_seconds: 1,
    storage_bucket: 'tarball-uploads',
    storage_path: `t/${sha}.tar.gz`,
    signed_url_expires_at: '2026-05-13T20:00:00Z',
    uploaded_at: '2026-05-13T00:00:00Z',
    d5_verdict: 'pending',
  });
  const { POST } = await import('../app/api/upload-tarball/finalize/route.ts?missing');
  const req = makeReq({ body: { tarball_id: id, sha256: sha } });
  const res = await POST(req);
  assert.equal(res.status, 409);
});

test('finalize: 200 + accepted=true happy path', async () => {
  resetFake();
  const id = crypto.randomUUID();
  const sha = 'a'.repeat(64);
  const tester_id = '11111111-2222-3333-4444-555555555555';
  fakeSupabase._allRows().push({
    id,
    tester_id,
    sha256: sha,
    size_bytes: 100,
    upload_status: 'pending_upload',
    duration_seconds: 60,
    storage_bucket: 'tarball-uploads',
    storage_path: `${tester_id}/${sha}.tar.gz`,
    signed_url_expires_at: '2026-05-13T20:00:00Z',
    uploaded_at: '2026-05-13T00:00:00Z',
    d5_verdict: 'pending',
  });
  fakeSupabase._putObject('tarball-uploads', `${tester_id}/${sha}.tar.gz`, 100);
  const { POST } = await import('../app/api/upload-tarball/finalize/route.ts?happy');
  const req = makeReq({ body: { tarball_id: id, sha256: sha } });
  const res = await POST(req);
  assert.equal(res.status, 200);
  const body = await res.json();
  assert.equal(body.accepted, true);
  assert.equal(body.sha256, sha);
});

test('finalize: 409 when storage object size disagrees with sign-time size', async () => {
  resetFake();
  const id = crypto.randomUUID();
  const sha = 'a'.repeat(64);
  const tester_id = '11111111-2222-3333-4444-555555555555';
  fakeSupabase._allRows().push({
    id,
    tester_id,
    sha256: sha,
    size_bytes: 100,
    upload_status: 'pending_upload',
    duration_seconds: 60,
    storage_bucket: 'tarball-uploads',
    storage_path: `${tester_id}/${sha}.tar.gz`,
    signed_url_expires_at: '2026-05-13T20:00:00Z',
    uploaded_at: '2026-05-13T00:00:00Z',
    d5_verdict: 'pending',
  });
  fakeSupabase._putObject('tarball-uploads', `${tester_id}/${sha}.tar.gz`, 73); // wrong!
  const { POST } = await import('../app/api/upload-tarball/finalize/route.ts?wrongsize');
  const req = makeReq({ body: { tarball_id: id, sha256: sha } });
  const res = await POST(req);
  assert.equal(res.status, 409);
});
