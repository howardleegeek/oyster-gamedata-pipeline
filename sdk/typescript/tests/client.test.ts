/**
 * Unit tests for BuyerClient using a mock fetch.
 */

import { describe, test, expect } from 'bun:test';
import { BuyerClient, BuyerClientError } from '../src/client.js';

interface FakeFetchResult {
  status: number;
  body: unknown;
  contentType?: string;
}

function fakeFetch(routes: Record<string, FakeFetchResult>): typeof fetch {
  return (async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = typeof input === 'string' ? input : input.toString();
    const key = `${init?.method ?? 'GET'} ${url}`;
    // Find first matching route (allow prefix match for query strings)
    let match: FakeFetchResult | undefined;
    for (const [route, result] of Object.entries(routes)) {
      if (key === route || key.startsWith(route)) {
        match = result;
        break;
      }
    }
    if (!match) {
      return new Response(JSON.stringify({ error: `no route for ${key}` }), {
        status: 404,
        headers: { 'content-type': 'application/json' },
      });
    }
    const body = typeof match.body === 'string' ? match.body : JSON.stringify(match.body);
    return new Response(body, {
      status: match.status,
      headers: { 'content-type': match.contentType ?? 'application/json' },
    });
  }) as unknown as typeof fetch;
}

describe('BuyerClient', () => {
  test('rejects missing baseUrl', () => {
    // @ts-expect-error testing runtime guard
    expect(() => new BuyerClient({})).toThrow(BuyerClientError);
  });

  test('list returns parsed items', async () => {
    const items = [
      {
        clip_id: 'c1',
        batch_id: 'b1',
        vendor_id: 'v1',
        size_bytes: 1024,
        sha256: 'abc',
        spec_version: 'v1',
        filename: 'c1.tar.gz',
        status: 'accepted',
        created_at: '2026-05-13T00:00:00Z',
      },
    ];
    const client = new BuyerClient({
      baseUrl: 'https://api.test.example/v1',
      apiKey: 'token',
      fetchImpl: fakeFetch({
        'GET https://api.test.example/v1/tarballs': {
          status: 200,
          body: { items, total: 1, limit: 100, offset: 0 },
        },
      }),
    });
    const result = await client.list();
    expect(result.items.length).toBe(1);
    expect(result.items[0]?.clip_id).toBe('c1');
    expect(result.total).toBe(1);
  });

  test('list respects filter params', async () => {
    let capturedUrl = '';
    const client = new BuyerClient({
      baseUrl: 'https://api.test.example/v1',
      apiKey: 'token',
      fetchImpl: (async (input: RequestInfo | URL) => {
        capturedUrl = typeof input === 'string' ? input : input.toString();
        return new Response(JSON.stringify({ items: [], total: 0, limit: 100, offset: 0 }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        });
      }) as unknown as typeof fetch,
    });
    await client.list({ batch_id: 'b1', status: 'accepted', limit: 50 });
    expect(capturedUrl).toContain('batch_id=b1');
    expect(capturedUrl).toContain('status=accepted');
    expect(capturedUrl).toContain('limit=50');
  });

  test('getMetadata returns object', async () => {
    const meta = {
      clip_id: 'c42',
      batch_id: 'b1',
      vendor_id: 'v1',
      size_bytes: 2048,
      sha256: 'def',
      spec_version: 'v1',
      filename: 'c42.tar.gz',
      status: 'accepted',
      created_at: '2026-05-13T00:00:00Z',
    };
    const client = new BuyerClient({
      baseUrl: 'https://api.test.example/v1',
      apiKey: 'token',
      fetchImpl: fakeFetch({
        'GET https://api.test.example/v1/tarballs/c42': { status: 200, body: meta },
      }),
    });
    const result = await client.getMetadata('c42');
    expect(result.clip_id).toBe('c42');
    expect(result.size_bytes).toBe(2048);
  });

  test('4xx errors are non-retryable', async () => {
    let calls = 0;
    const client = new BuyerClient({
      baseUrl: 'https://api.test.example/v1',
      apiKey: 'token',
      maxRetries: 3,
      fetchImpl: (async () => {
        calls++;
        return new Response(JSON.stringify({ error: 'forbidden' }), {
          status: 403,
          headers: { 'content-type': 'application/json' },
        });
      }) as unknown as typeof fetch,
    });
    await expect(client.getMetadata('xxx')).rejects.toThrow(BuyerClientError);
    expect(calls).toBe(1);
  });
});
