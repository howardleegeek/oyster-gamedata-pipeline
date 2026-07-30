/**
 * Minimal tarball reader — no third-party deps.
 *
 * Reads gzipped POSIX tar archives via Node 18+ stdlib (`zlib`, `fs`).
 * Sufficient for buyer-spec v1 needs (open, list entries, extract entries
 * to disk) and rejects path-traversal entries (CVE-2007-4559).
 *
 * Implementation is buffer-based (whole archive into memory then iterate)
 * rather than streaming. Buyer tarballs are 0.5–1.5 GB so this needs the
 * Node process to be sized appropriately; in practice buyer CI runs on
 * machines with multi-GB RAM and this avoids streaming parser bugs.
 */

import * as fs from 'node:fs';
import * as path from 'node:path';
import * as zlib from 'node:zlib';
import { pipeline } from 'node:stream/promises';

export async function isTarballPath(p: string): Promise<boolean> {
  const lower = p.toLowerCase();
  return lower.endsWith('.tar.gz') || lower.endsWith('.tgz') || lower.endsWith('.tar');
}

export interface ExtractResult {
  /** Top-level entry names directly under the clip root. */
  entries: string[];
  /** Path to the directory that actually contains the clip files. */
  extractedRoot: string;
}

/**
 * Extract a tarball into `targetDir` and return entry listing.
 *
 * Handles two valid layouts:
 *  1. `tarball/<files at root>` — released sample format
 *  2. `tarball/<clip_id>/<files>` — vendor submission format
 */
export async function extractTarballStructure(
  tarballPath: string,
  targetDir: string,
): Promise<ExtractResult> {
  await fs.promises.mkdir(targetDir, { recursive: true });
  const lower = tarballPath.toLowerCase();
  const isGzip = lower.endsWith('.gz') || lower.endsWith('.tgz');

  // Load the whole file into memory. For buyer tarballs (≤ 1.5 GB) this is
  // acceptable on a CI machine and avoids streaming-parser edge cases.
  let raw = await fs.promises.readFile(tarballPath);
  if (isGzip) {
    raw = zlib.gunzipSync(raw);
  }

  await extractRawTarBuffer(raw, targetDir);

  // Find the clip root (may be one level deep).
  const top = await fs.promises.readdir(targetDir);
  if (top.includes('video.mp4')) {
    return { entries: top, extractedRoot: targetDir };
  }
  for (const name of top) {
    const full = path.join(targetDir, name);
    if ((await fs.promises.stat(full)).isDirectory()) {
      const inside = await fs.promises.readdir(full);
      if (inside.includes('video.mp4')) {
        return { entries: inside, extractedRoot: full };
      }
    }
  }
  throw new Error(
    `could not locate video.mp4 in tarball at ${tarballPath} (top entries: ${top.join(', ')})`,
  );
}

/**
 * Decode a raw POSIX tar archive in memory and write each entry to disk.
 *
 * Handles typeflag '0' (regular file), '5' (directory), and '' (legacy
 * regular). Rejects absolute and `..` paths.
 */
async function extractRawTarBuffer(buf: Buffer, targetDir: string): Promise<void> {
  const BLOCK = 512;
  const target = path.resolve(targetDir);
  let offset = 0;

  while (offset + BLOCK <= buf.length) {
    const headerBlock = buf.subarray(offset, offset + BLOCK);
    offset += BLOCK;

    // All-zero block marks end of archive.
    if (isAllZero(headerBlock)) continue;

    const header = parseTarHeader(headerBlock);
    if (!header.name) continue;

    const dest = path.resolve(target, header.name);
    if (!dest.startsWith(target + path.sep) && dest !== target) {
      throw new Error(`refusing path-traversal entry: ${header.name}`);
    }

    if (header.typeflag === '5' || header.name.endsWith('/')) {
      await fs.promises.mkdir(dest, { recursive: true });
      continue;
    }

    await fs.promises.mkdir(path.dirname(dest), { recursive: true });

    if (header.size === 0) {
      await fs.promises.writeFile(dest, '');
      continue;
    }

    if (offset + header.size > buf.length) {
      throw new Error(
        `truncated tarball: entry ${header.name} claims ${header.size} bytes but only ${buf.length - offset} remain`,
      );
    }
    const data = buf.subarray(offset, offset + header.size);
    await fs.promises.writeFile(dest, data);
    offset += header.size;

    // Advance past the trailing padding to next 512-byte boundary.
    const pad = (BLOCK - (header.size % BLOCK)) % BLOCK;
    offset += pad;
  }
}

function isAllZero(b: Buffer): boolean {
  for (let i = 0; i < b.length; i++) {
    if (b[i] !== 0) return false;
  }
  return true;
}

interface TarHeader {
  name: string;
  size: number;
  typeflag: string;
}

function parseTarHeader(block: Buffer): TarHeader {
  const name = block.subarray(0, 100).toString('utf8').replace(/\0.*$/, '');
  const sizeOctal = block.subarray(124, 136).toString('utf8').replace(/\0.*$/, '').trim();
  const size = sizeOctal ? parseInt(sizeOctal, 8) : 0;
  const typeflag = block.subarray(156, 157).toString('utf8') || '0';
  // ustar prefix (long paths).
  const prefix = block.subarray(345, 500).toString('utf8').replace(/\0.*$/, '');
  return {
    name: prefix ? `${prefix}/${name}` : name,
    size,
    typeflag,
  };
}

// Stream-pipeline re-export for callers (avoids dual imports).
export { pipeline };
