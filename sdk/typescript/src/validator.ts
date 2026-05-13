/**
 * Local-only validator for the buyer-spec v1 tarball format.
 *
 * The TS SDK doesn't bundle a full 24-criterion port of the Python lint
 * script (that would mean re-implementing OpenEXR / video decode in JS).
 * Instead we expose **structural** + **schema** validation: open the
 * tarball, check the 5 required entries, parse systeminfo + action_camera,
 * count depth frames, and produce a JSON report the buyer can include in
 * CI gates.
 *
 * For the full 24-criterion lint, buyers should call the Python SDK or
 * the `oyster-gamedata validate` CLI.
 */

import * as fs from 'node:fs';
import * as path from 'node:path';
import { extractTarballStructure, isTarballPath } from './tar.js';
import {
  parseActionCamera,
  parseSysteminfo,
  SchemaValidationError,
  type ActionCameraFrame,
  type Systeminfo,
} from './schema.js';

export interface LocalValidateOptions {
  /**
   * If `true`, the validator does not extract the tarball to disk — it
   * scans entries in-place and reads only the small JSON files.
   * Default: `true` (lightweight).
   */
  lightweight?: boolean;
  /** When set, write the JSON report to this path. */
  outputPath?: string;
}

export interface LocalValidateCriterion {
  id: number;
  name: string;
  passed: boolean;
  message: string;
  details?: Record<string, unknown>;
}

export interface LocalValidateReport {
  source: string;
  passed: boolean;
  summary: {
    total: number;
    passed: number;
    failed: number;
    pass_rate: string;
    status: 'PASS' | 'FAIL';
  };
  results: LocalValidateCriterion[];
  /** Parsed systeminfo if available. */
  systeminfo?: Systeminfo;
  /** First N action_camera frames (sample). */
  action_camera_sample?: ActionCameraFrame[];
}

const REQUIRED_FILES = ['video.mp4', 'systeminfo.json', 'action_camera.json', 'gameinfo.xlsx'];

export async function validateLocalTarball(
  localPath: string,
  opts: LocalValidateOptions = {},
): Promise<LocalValidateReport> {
  // `lightweight` was reserved for an in-memory mode; current implementation
  // always extracts to a tempdir because reading EXR/xlsx without extraction
  // would require a streaming tar parser anyway. Kept on the public API.
  void opts.lightweight;
  const results: LocalValidateCriterion[] = [];
  let systeminfo: Systeminfo | undefined;
  let actionSample: ActionCameraFrame[] | undefined;

  const stat = await fs.promises.stat(localPath).catch(() => null);
  if (!stat) {
    throw new Error(`path not found: ${localPath}`);
  }

  let clipRoot: string;
  let cleanup: (() => Promise<void>) | undefined;
  let entryNames: string[];

  if (stat.isDirectory()) {
    clipRoot = path.resolve(localPath);
    entryNames = await fs.promises.readdir(clipRoot);
  } else if (await isTarballPath(localPath)) {
    const os = await import('node:os');
    const tmp = await fs.promises.mkdtemp(path.join(os.tmpdir(), 'oyster-sdk-'));
    const { entries, extractedRoot } = await extractTarballStructure(localPath, tmp);
    clipRoot = extractedRoot;
    entryNames = entries;
    cleanup = async () => fs.promises.rm(tmp, { recursive: true, force: true });
  } else {
    throw new Error(`not a tarball or directory: ${localPath}`);
  }

  try {
    // 1. Required files
    for (let i = 0; i < REQUIRED_FILES.length; i++) {
      const f = REQUIRED_FILES[i]!;
      const present = entryNames.includes(f);
      const absPath = path.join(clipRoot, f);
      let size = 0;
      try {
        size = (await fs.promises.stat(absPath)).size;
      } catch {
        /* not present */
      }
      results.push({
        id: i + 1,
        name: `${f} present and non-empty`,
        passed: present && size > 0,
        message: present ? `${size} bytes` : `missing ${f}`,
        details: { size_bytes: size },
      });
    }

    // 2. Required dirs
    const depthDir = path.join(clipRoot, 'depth');
    let depthCount = 0;
    try {
      const entries = await fs.promises.readdir(depthDir);
      depthCount = entries.filter((e) => e.toLowerCase().endsWith('.exr')).length;
    } catch {
      /* missing */
    }
    results.push({
      id: REQUIRED_FILES.length + 1,
      name: `depth/*.exr present`,
      passed: depthCount > 0,
      message: depthCount > 0 ? `${depthCount} EXR files` : 'no depth/*.exr',
      details: { count: depthCount },
    });

    // 3. systeminfo.json schema
    try {
      const data = JSON.parse(await fs.promises.readFile(path.join(clipRoot, 'systeminfo.json'), 'utf8'));
      systeminfo = parseSysteminfo(data);
      results.push({
        id: REQUIRED_FILES.length + 2,
        name: 'systeminfo.json schema',
        passed: true,
        message: `${systeminfo.gameProcessName} @ ${systeminfo.width}x${systeminfo.height}`,
      });
    } catch (err) {
      results.push({
        id: REQUIRED_FILES.length + 2,
        name: 'systeminfo.json schema',
        passed: false,
        message: err instanceof Error ? err.message : String(err),
      });
    }

    // 4. action_camera.json schema (parse + sample 5 frames)
    try {
      const data = JSON.parse(
        await fs.promises.readFile(path.join(clipRoot, 'action_camera.json'), 'utf8'),
      );
      const frames = parseActionCamera(data, { strict: true });
      actionSample = frames.slice(0, 5);
      // Check frame continuity (criterion gate 4)
      const gaps: number[] = [];
      for (let i = 1; i < frames.length; i++) {
        const prev = frames[i - 1];
        const cur = frames[i];
        if (!prev || !cur) continue;
        if (cur.frame !== prev.frame + 1) gaps.push(cur.frame);
      }
      results.push({
        id: REQUIRED_FILES.length + 3,
        name: 'action_camera.json schema',
        passed: true,
        message: `${frames.length} frames parsed`,
        details: { frame_count: frames.length },
      });
      results.push({
        id: REQUIRED_FILES.length + 4,
        name: 'action_camera frame continuity (no gaps)',
        passed: gaps.length === 0,
        message: gaps.length === 0 ? 'sequential' : `${gaps.length} gaps`,
        details: { first_gaps: gaps.slice(0, 5) },
      });
      // Check fx==fy (criterion 8)
      const fxFyMismatch = frames.filter(
        (f) => f.camera_intrinsics.fx !== f.camera_intrinsics.fy,
      ).length;
      results.push({
        id: REQUIRED_FILES.length + 5,
        name: 'camera_intrinsics fx == fy (pinhole)',
        passed: fxFyMismatch === 0,
        message: fxFyMismatch === 0 ? 'pinhole ok' : `${fxFyMismatch} frames violate fx==fy`,
      });
    } catch (err) {
      results.push({
        id: REQUIRED_FILES.length + 3,
        name: 'action_camera.json schema',
        passed: false,
        message: err instanceof SchemaValidationError ? err.message : String(err),
      });
    }

    const passedCount = results.filter((r) => r.passed).length;
    const total = results.length;
    const report: LocalValidateReport = {
      source: localPath,
      passed: passedCount === total,
      summary: {
        total,
        passed: passedCount,
        failed: total - passedCount,
        pass_rate: `${((100 * passedCount) / total).toFixed(1)}%`,
        status: passedCount === total ? 'PASS' : 'FAIL',
      },
      results,
      systeminfo,
      action_camera_sample: actionSample,
    };

    if (opts.outputPath) {
      await fs.promises.writeFile(opts.outputPath, JSON.stringify(report, null, 2));
    }
    return report;
  } finally {
    if (cleanup) await cleanup();
  }
}
