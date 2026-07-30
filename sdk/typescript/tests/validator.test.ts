/**
 * Validator integration tests — runs against the released sample tarball
 * and against a synthetic minimal-clip directory.
 */

import { describe, test, expect, beforeAll } from 'bun:test';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import { validateLocalTarball } from '../src/validator.js';

const REPO_ROOT = path.resolve(__dirname, '..', '..', '..');
const SAMPLE = path.join(REPO_ROOT, 'samples', 'buyer-spec-v1-rc1.tar.gz');

function mkFrame(idx: number, routeType: number = 1): Record<string, unknown> {
  return {
    frame: idx,
    time: `2026-05-02 12:00:${String(idx).padStart(2, '0')}.000`,
    fps: 30.0,
    route_type: routeType,
    mouse_x: 0.5,
    mouse_y: 0.5,
    mouse_dx: 0.0,
    mouse_dy: 0.0,
    keyCode: [87],
    camera_position: [idx, 64.0, 0.0],
    camera_rotation_oula: [0.0, 0.0, 0.0],
    camera_rotation_quaternion: [0.0, 0.0, 0.0, 1.0],
    'camera_Follow Offset': [0.0, 1.6, 0.0],
    camera_intrinsics: { fx: 960.0, fy: 960.0, cx: 960.0, cy: 540.0 },
    camera_speed: [1.5, 0.0, 0.0],
    player_position: [idx, 64.0, 0.0],
    player_rotation_oula: [0.0, 0.0, 0.0],
    player_rotation_quaternion: [0.0, 0.0, 0.0, 1.0],
    player_speed: [1.5, 0.0, 0.0],
    metric_scale: 1.0,
  };
}

describe('validateLocalTarball (directory)', () => {
  let clipDir: string;

  beforeAll(async () => {
    clipDir = fs.mkdtempSync(path.join(os.tmpdir(), 'oyster-ts-test-'));
    fs.writeFileSync(path.join(clipDir, 'video.mp4'), Buffer.alloc(64, 1));
    fs.writeFileSync(
      path.join(clipDir, 'systeminfo.json'),
      JSON.stringify({
        gameProcessName: 'test.exe',
        x: 0,
        y: 0,
        width: 1920,
        height: 1080,
        recordDpi: 1.0,
        map_scale: 1.0,
        map_bounds: { min_x: -100, min_z: -100, max_x: 100, max_z: 100 },
      }),
    );
    fs.writeFileSync(
      path.join(clipDir, 'action_camera.json'),
      JSON.stringify([mkFrame(0), mkFrame(1), mkFrame(2, 2)]),
    );
    fs.writeFileSync(path.join(clipDir, 'gameinfo.xlsx'), Buffer.from('PK\x03\x04stub'));
    fs.mkdirSync(path.join(clipDir, 'depth'));
    fs.writeFileSync(path.join(clipDir, 'depth', 'depth_000000.exr'), Buffer.alloc(32, 2));
  });

  test('passes on synthetic clip', async () => {
    const report = await validateLocalTarball(clipDir);
    expect(report.passed).toBe(true);
    expect(report.summary.status).toBe('PASS');
    expect(report.systeminfo?.width).toBe(1920);
    expect(report.action_camera_sample?.length).toBe(3);
  });

  test('reports failure for missing video', async () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'oyster-ts-test-'));
    fs.writeFileSync(
      path.join(dir, 'systeminfo.json'),
      JSON.stringify({
        gameProcessName: 'x.exe',
        x: 0,
        y: 0,
        width: 1920,
        height: 1080,
        recordDpi: 1.0,
        map_scale: 1.0,
        map_bounds: { min_x: -100, min_z: -100, max_x: 100, max_z: 100 },
      }),
    );
    fs.writeFileSync(path.join(dir, 'action_camera.json'), '[]');
    fs.writeFileSync(path.join(dir, 'gameinfo.xlsx'), 'x');
    fs.mkdirSync(path.join(dir, 'depth'));
    const report = await validateLocalTarball(dir);
    expect(report.passed).toBe(false);
    const videoCriterion = report.results.find((r) => r.name.startsWith('video.mp4'));
    expect(videoCriterion?.passed).toBe(false);
  });

  test('detects fx != fy', async () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'oyster-ts-test-'));
    fs.writeFileSync(path.join(dir, 'video.mp4'), Buffer.alloc(8));
    fs.writeFileSync(
      path.join(dir, 'systeminfo.json'),
      JSON.stringify({
        gameProcessName: 'x.exe',
        x: 0,
        y: 0,
        width: 1920,
        height: 1080,
        recordDpi: 1.0,
        map_scale: 1.0,
        map_bounds: { min_x: -100, min_z: -100, max_x: 100, max_z: 100 },
      }),
    );
    const badFrame = mkFrame(0);
    badFrame.camera_intrinsics = { fx: 960.0, fy: 800.0, cx: 960.0, cy: 540.0 };
    fs.writeFileSync(path.join(dir, 'action_camera.json'), JSON.stringify([badFrame]));
    fs.writeFileSync(path.join(dir, 'gameinfo.xlsx'), 'x');
    fs.mkdirSync(path.join(dir, 'depth'));
    fs.writeFileSync(path.join(dir, 'depth', '0.exr'), Buffer.alloc(8));
    const report = await validateLocalTarball(dir);
    const pinholeCriterion = report.results.find((r) => r.name.includes('pinhole'));
    expect(pinholeCriterion?.passed).toBe(false);
  });
});

describe('validateLocalTarball (real .tar.gz)', () => {
  test.if(fs.existsSync(SAMPLE))('passes on released sample', async () => {
    const report = await validateLocalTarball(SAMPLE);
    expect(report.systeminfo?.gameProcessName).toBe('minecraft.exe');
    expect(report.summary.total).toBeGreaterThan(0);
    // The released sample tarball must pass every structural+schema check.
    // Logs the failing criteria so flaky CI failures are debuggable.
    if (!report.passed) {
      for (const r of report.results.filter((x) => !x.passed)) {
        console.error(`  [FAIL ${r.id}] ${r.name}: ${r.message}`, r.details ?? '');
      }
    }
    expect(report.passed).toBe(true);
  }, 120_000);
});
