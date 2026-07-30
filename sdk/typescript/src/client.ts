/**
 * BuyerClient — thin HTTP client for the Oyster GameData buyer download API.
 *
 * Pure Node 18+ stdlib (uses `fetch` global, `node:fs`, `node:zlib`).
 * No third-party deps so the buyer can drop the SDK into any toolchain.
 */

import * as fs from 'node:fs';
import * as path from 'node:path';
import { pipeline } from 'node:stream/promises';
import type { TarballMetadata } from './schema.js';
import { validateLocalTarball, type LocalValidateOptions, type LocalValidateReport } from './validator.js';

export class BuyerClientError extends Error {
  constructor(message: string, public readonly statusCode?: number, public readonly body?: unknown) {
    super(message);
    this.name = 'BuyerClientError';
  }
}

export interface BuyerClientOptions {
  /** API base URL, e.g. `https://api.oysterworld.dev/buyer/v1`. */
  baseUrl: string;
  /** Bearer token for buyer authentication. */
  apiKey?: string;
  /** Per-request timeout in milliseconds (default 30000). */
  timeoutMs?: number;
  /** Number of retries on transient (5xx / network) errors (default 3). */
  maxRetries?: number;
  /** Custom fetch (mainly for testing). */
  fetchImpl?: typeof fetch;
}

export interface ListOptions {
  batch_id?: string;
  vendor_id?: string;
  status?: TarballMetadata['status'];
  limit?: number;
  offset?: number;
}

export interface ListResult {
  items: TarballMetadata[];
  total: number;
  limit: number;
  offset: number;
}

export interface DownloadOptions {
  /** Destination directory; the file is written as `{filename}`. */
  outputDir: string;
  /** Override filename; defaults to the server-provided name. */
  filename?: string;
  /** Skip SHA-256 verification (NOT recommended). */
  skipChecksum?: boolean;
  /** Optional progress callback. */
  onProgress?: (downloaded: number, total: number) => void;
}

export interface DownloadResult {
  /** Absolute path of the saved tarball. */
  path: string;
  /** Size in bytes. */
  size_bytes: number;
  /** Whether checksum verification passed (or was skipped). */
  checksum_ok: boolean;
}

export class BuyerClient {
  private readonly baseUrl: string;
  private readonly apiKey?: string;
  private readonly timeoutMs: number;
  private readonly maxRetries: number;
  private readonly fetchImpl: typeof fetch;

  constructor(opts: BuyerClientOptions) {
    if (!opts.baseUrl) throw new BuyerClientError('baseUrl is required');
    this.baseUrl = opts.baseUrl.replace(/\/+$/, '');
    this.apiKey = opts.apiKey;
    this.timeoutMs = opts.timeoutMs ?? 30_000;
    this.maxRetries = opts.maxRetries ?? 3;
    this.fetchImpl = opts.fetchImpl ?? (globalThis.fetch as typeof fetch);
    if (!this.fetchImpl) {
      throw new BuyerClientError('global fetch is not available; pass fetchImpl or upgrade to Node 18+');
    }
  }

  /** GET /tarballs — list available clips, optionally filtered. */
  async list(opts: ListOptions = {}): Promise<ListResult> {
    const qs = new URLSearchParams();
    if (opts.batch_id) qs.set('batch_id', opts.batch_id);
    if (opts.vendor_id) qs.set('vendor_id', opts.vendor_id);
    if (opts.status) qs.set('status', opts.status);
    if (opts.limit !== undefined) qs.set('limit', String(opts.limit));
    if (opts.offset !== undefined) qs.set('offset', String(opts.offset));
    const qstr = qs.toString();
    const body = await this._request('GET', `/tarballs${qstr ? `?${qstr}` : ''}`);
    return this._parseListResult(body);
  }

  /** GET /tarballs/{clip_id} — fetch metadata for one clip. */
  async getMetadata(clipId: string): Promise<TarballMetadata> {
    return (await this._request('GET', `/tarballs/${encodeURIComponent(clipId)}`)) as TarballMetadata;
  }

  /**
   * Download a clip's tarball to disk.
   *
   * If the metadata returned by the server doesn't include a presigned
   * URL, the SDK calls `/tarballs/{id}/download-url` to obtain one.
   */
  async download(clipId: string, opts: DownloadOptions): Promise<DownloadResult> {
    const metadata = await this.getMetadata(clipId);

    let downloadUrl = metadata.download_url;
    if (!downloadUrl) {
      const urlBody = await this._request(
        'POST',
        `/tarballs/${encodeURIComponent(clipId)}/download-url`,
      );
      downloadUrl = (urlBody as { url?: string }).url;
      if (!downloadUrl) {
        throw new BuyerClientError('Server did not return a presigned URL');
      }
    }

    const filename = opts.filename ?? metadata.filename;
    const outPath = path.resolve(opts.outputDir, filename);
    await fs.promises.mkdir(opts.outputDir, { recursive: true });

    const res = await this.fetchImpl(downloadUrl);
    if (!res.ok) {
      throw new BuyerClientError(`download failed: HTTP ${res.status}`, res.status);
    }
    if (!res.body) {
      throw new BuyerClientError('download response has no body');
    }
    const total = Number(res.headers.get('content-length') ?? metadata.size_bytes ?? 0);

    // Stream to a temp file then rename.
    const tmpPath = `${outPath}.tmp`;
    const sink = fs.createWriteStream(tmpPath);
    let downloaded = 0;
    if (opts.onProgress) {
      const onProgress = opts.onProgress;
      const reader = (res.body as ReadableStream<Uint8Array>).getReader();
      const stream = new ReadableStream<Uint8Array>({
        async pull(controller) {
          const { value, done } = await reader.read();
          if (done) {
            controller.close();
            return;
          }
          if (value) {
            downloaded += value.byteLength;
            onProgress(downloaded, total);
            controller.enqueue(value);
          }
        },
      });
      await pipeline(stream as unknown as NodeJS.ReadableStream, sink);
    } else {
      await pipeline(res.body as unknown as NodeJS.ReadableStream, sink);
    }
    await fs.promises.rename(tmpPath, outPath);

    let checksum_ok = false;
    if (!opts.skipChecksum && metadata.sha256) {
      const actual = await sha256File(outPath);
      checksum_ok = actual === metadata.sha256;
      if (!checksum_ok) {
        throw new BuyerClientError(
          `checksum mismatch: expected ${metadata.sha256}, got ${actual}`,
        );
      }
    } else {
      checksum_ok = !!opts.skipChecksum;
    }

    const stat = await fs.promises.stat(outPath);
    return { path: outPath, size_bytes: stat.size, checksum_ok };
  }

  /**
   * Validate an already-downloaded local tarball or extracted directory.
   *
   * NOTE: This does NOT call the server — it runs the structural and
   * schema validators locally. Buyers can use it offline.
   */
  async validate(localPath: string, opts: LocalValidateOptions = {}): Promise<LocalValidateReport> {
    return validateLocalTarball(localPath, opts);
  }

  // ----- internals -------------------------------------------------------

  private _parseListResult(body: unknown): ListResult {
    if (body && typeof body === 'object' && 'items' in body) {
      const b = body as { items: TarballMetadata[]; total?: number; limit?: number; offset?: number };
      return {
        items: b.items,
        total: b.total ?? b.items.length,
        limit: b.limit ?? b.items.length,
        offset: b.offset ?? 0,
      };
    }
    throw new BuyerClientError('list: unexpected response shape', undefined, body);
  }

  private async _request(method: string, pathFragment: string, body?: unknown): Promise<unknown> {
    const url = `${this.baseUrl}${pathFragment}`;
    const headers: Record<string, string> = { Accept: 'application/json' };
    if (this.apiKey) headers.Authorization = `Bearer ${this.apiKey}`;
    if (body !== undefined) headers['Content-Type'] = 'application/json';

    let lastErr: Error | undefined;
    for (let attempt = 0; attempt <= this.maxRetries; attempt++) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), this.timeoutMs);
      try {
        const res = await this.fetchImpl(url, {
          method,
          headers,
          body: body !== undefined ? JSON.stringify(body) : undefined,
          signal: controller.signal,
        });
        clearTimeout(timer);
        if (res.status >= 200 && res.status < 300) {
          if (res.status === 204) return null;
          const ct = res.headers.get('content-type') ?? '';
          if (ct.includes('application/json')) return await res.json();
          return await res.text();
        }
        const errBody = await safeJson(res);
        // 4xx → non-retryable
        if (res.status >= 400 && res.status < 500) {
          throw new BuyerClientError(
            (errBody as { error?: string })?.error ?? `HTTP ${res.status}`,
            res.status,
            errBody,
          );
        }
        lastErr = new BuyerClientError(`HTTP ${res.status}`, res.status, errBody);
      } catch (err) {
        clearTimeout(timer);
        if (err instanceof BuyerClientError && err.statusCode && err.statusCode < 500) {
          throw err;
        }
        lastErr = err as Error;
      }
      if (attempt < this.maxRetries) {
        const delay = Math.min(1000 * 2 ** attempt, 10_000);
        await new Promise((r) => setTimeout(r, delay));
      }
    }
    throw lastErr ?? new BuyerClientError('Max retries exceeded');
  }
}

async function safeJson(res: Response): Promise<unknown> {
  try {
    return await res.json();
  } catch {
    return null;
  }
}

async function sha256File(filePath: string): Promise<string> {
  const { createHash } = await import('node:crypto');
  const hash = createHash('sha256');
  const stream = fs.createReadStream(filePath);
  for await (const chunk of stream) {
    hash.update(chunk);
  }
  return hash.digest('hex');
}
