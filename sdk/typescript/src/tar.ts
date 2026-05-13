/**
 * Minimal tarball reader — no third-party deps.
 *
 * Reads gzipped POSIX tar archives via Node 18+ stdlib (`zlib`, `fs`).
 * Sufficient for buyer-spec v1 needs (open, list entries, extract entries
 * to disk) and rejects path-traversal entries (CVE-2007-4559).
 */

import * as fs from 'node:fs';
import * as path from 'node:path';
import { createGunzip } from 'node:zlib';
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

  let raw: NodeJS.ReadableStream = fs.createReadStream(tarballPath);
  if (isGzip) {
    const gunzip = createGunzip();
    raw.pipe(gunzip);
    raw = gunzip;
  }

  await extractRawTar(raw, targetDir);

  // Find the clip root (may be one level deep).
  const top = await fs.promises.readdir(targetDir);
  if (top.includes('video.mp4')) {
    return { entries: top, extractedRoot: targetDir };
  }
  const dirs: string[] = [];
  for (const name of top) {
    const full = path.join(targetDir, name);
    if ((await fs.promises.stat(full)).isDirectory()) {
      const inside = await fs.promises.readdir(full);
      if (inside.includes('video.mp4')) {
        return { entries: inside, extractedRoot: full };
      }
      dirs.push(name);
    }
  }
  throw new Error(
    `could not locate video.mp4 in tarball at ${tarballPath} (top entries: ${top.join(', ')})`,
  );
}

/**
 * Stream-decode a raw POSIX tar archive and write each regular file to disk.
 *
 * Implementation is the minimal parser sufficient for buyer-spec tarballs:
 *  - Reads 512-byte header blocks.
 *  - Supports typeflag '0' (regular file), '5' (directory), and '' (legacy).
 *  - Refuses absolute and `..` paths.
 */
async function extractRawTar(stream: NodeJS.ReadableStream, targetDir: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const BLOCK = 512;
    const target = path.resolve(targetDir);
    let buffer = Buffer.alloc(0);
    let headerPending = true;
    let header: TarHeader | null = null;
    let remainingBytes = 0;
    let openFile: fs.WriteStream | null = null;

    const cleanup = (err?: Error): void => {
      if (openFile) {
        openFile.destroy();
        openFile = null;
      }
      if (err) reject(err);
      else resolve();
    };

    stream.on('data', (chunkRaw: Buffer | string) => {
      const chunk = typeof chunkRaw === 'string' ? Buffer.from(chunkRaw) : chunkRaw;
      buffer = Buffer.concat([buffer, chunk]);

      try {
        while (true) {
          if (headerPending) {
            if (buffer.length < BLOCK) break;
            const block = buffer.subarray(0, BLOCK);
            buffer = buffer.subarray(BLOCK);

            // All-zero header → end of archive.
            if (block.every((b) => b === 0)) continue;

            header = parseTarHeader(block);
            if (!header.name) continue;

            // Reject path-traversal.
            const dest = path.resolve(target, header.name);
            if (!dest.startsWith(target + path.sep) && dest !== target) {
              throw new Error(`refusing path-traversal entry: ${header.name}`);
            }

            if (header.typeflag === '5' || header.name.endsWith('/')) {
              fs.mkdirSync(dest, { recursive: true });
              headerPending = true;
              continue;
            }

            // Ensure parent dir.
            fs.mkdirSync(path.dirname(dest), { recursive: true });

            if (header.size === 0) {
              fs.writeFileSync(dest, '');
              headerPending = true;
              continue;
            }

            openFile = fs.createWriteStream(dest);
            remainingBytes = header.size;
            headerPending = false;
          }

          if (!headerPending) {
            if (buffer.length === 0) break;
            const toWrite = Math.min(buffer.length, remainingBytes);
            openFile!.write(buffer.subarray(0, toWrite));
            buffer = buffer.subarray(toWrite);
            remainingBytes -= toWrite;
            if (remainingBytes === 0) {
              openFile!.end();
              openFile = null;
              // Skip padding to next 512-byte boundary.
              const pad = (BLOCK - (header!.size % BLOCK)) % BLOCK;
              if (buffer.length >= pad) {
                buffer = buffer.subarray(pad);
                headerPending = true;
              } else {
                // Mark a pending skip
                const need = pad - buffer.length;
                buffer = Buffer.alloc(0);
                remainingBytes = -need;
                headerPending = false;
              }
            } else if (remainingBytes < 0) {
              // Burning padding from previous file
              const need = -remainingBytes;
              const skip = Math.min(buffer.length, need);
              buffer = buffer.subarray(skip);
              remainingBytes += skip;
              if (remainingBytes === 0) headerPending = true;
            }
          }
        }
      } catch (err) {
        stream.removeAllListeners();
        cleanup(err as Error);
      }
    });

    stream.on('end', () => cleanup());
    stream.on('error', (err) => cleanup(err));
  });
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
  // ustar prefix
  const prefix = block.subarray(345, 500).toString('utf8').replace(/\0.*$/, '');
  return {
    name: prefix ? `${prefix}/${name}` : name,
    size,
    typeflag,
  };
}

// Stream-pipeline re-export for callers (avoids dual imports).
export { pipeline };
