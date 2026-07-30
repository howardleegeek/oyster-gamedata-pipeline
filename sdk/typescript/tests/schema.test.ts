/**
 * Unit tests for the TS schema parsers. Runs under both `bun test` and Vitest.
 */

import { describe, test, expect } from 'bun:test';
import {
  parseVector3,
  parseVector4,
  parseSysteminfo,
  parseActionCameraFrame,
  parseActionCamera,
  isPinhole,
  SchemaValidationError,
} from '../src/schema.js';

describe('parseVector3', () => {
  test('accepts array form', () => {
    expect(parseVector3([1, 2, 3])).toEqual({ x: 1, y: 2, z: 3 });
  });
  test('accepts object form', () => {
    expect(parseVector3({ x: 1, y: 2, z: 3 })).toEqual({ x: 1, y: 2, z: 3 });
  });
  test('rejects 4-element array', () => {
    expect(() => parseVector3([1, 2, 3, 4])).toThrow(SchemaValidationError);
  });
  test('rejects missing key', () => {
    expect(() => parseVector3({ x: 1, y: 2 })).toThrow(SchemaValidationError);
  });
  test('rejects non-numeric', () => {
    expect(() => parseVector3(['a', 'b', 'c'])).toThrow(SchemaValidationError);
  });
});

describe('parseVector4', () => {
  test('accepts array form', () => {
    expect(parseVector4([0, 1, 0, 1])).toEqual({ x: 0, y: 1, z: 0, w: 1 });
  });
  test('accepts object form', () => {
    expect(parseVector4({ x: 0, y: 0, z: 0, w: 1 })).toEqual({ x: 0, y: 0, z: 0, w: 1 });
  });
  test('rejects 3-element array', () => {
    expect(() => parseVector4([1, 2, 3])).toThrow(SchemaValidationError);
  });
});

describe('parseSysteminfo', () => {
  const valid = {
    gameProcessName: 'minecraft.exe',
    x: 0,
    y: 0,
    width: 1920,
    height: 1080,
    recordDpi: 1.0,
    map_scale: 1.0,
    map_bounds: { min_x: -10000, min_z: -10000, max_x: 10000, max_z: 10000 },
  };

  test('parses canonical payload', () => {
    const si = parseSysteminfo(valid);
    expect(si.gameProcessName).toBe('minecraft.exe');
    expect(si.width).toBe(1920);
    expect(si.map_bounds.min_x).toBe(-10000);
  });

  test('preserves extra fields', () => {
    const si = parseSysteminfo({ ...valid, custom: 'value' });
    expect(si.custom).toBe('value');
  });

  test('rejects missing width', () => {
    const { width, ...rest } = valid;
    void width;
    expect(() => parseSysteminfo(rest)).toThrow(SchemaValidationError);
  });

  test('rejects bad map_bounds', () => {
    expect(() => parseSysteminfo({ ...valid, map_bounds: { min_x: -1 } })).toThrow(
      SchemaValidationError,
    );
  });
});

describe('parseActionCameraFrame', () => {
  const validFrame = {
    frame: 0,
    time: '2026-05-02 12:00:00.000',
    fps: 30.0,
    route_type: 1,
    mouse_x: 0.5,
    mouse_y: 0.5,
    mouse_dx: 0.01,
    mouse_dy: -0.02,
    keyCode: [87],
    camera_position: [0.0, 64.0, 0.0],
    camera_rotation_oula: [0.0, -180.0, 0.0],
    camera_rotation_quaternion: [0.0, -1.0, 0.0, 0.0],
    'camera_Follow Offset': [0.0, 1.6, 0.0],
    camera_intrinsics: { fx: 960.0, fy: 960.0, cx: 960.0, cy: 540.0 },
    camera_speed: [1.5, 0.0, 0.0],
    player_position: [0.0, 64.0, 0.0],
    player_rotation_oula: [0.0, -180.0, 0.0],
    player_rotation_quaternion: [0.0, -1.0, 0.0, 0.0],
    player_speed: [1.5, 0.0, 0.0],
    metric_scale: 1.0,
  };

  test('parses sample-tarball frame', () => {
    const f = parseActionCameraFrame(validFrame);
    expect(f.frame).toBe(0);
    expect(f.fps).toBe(30);
    expect(f.keyCode).toEqual([87]);
    expect(f.camera_position).toEqual({ x: 0, y: 64, z: 0 });
    expect(isPinhole(f.camera_intrinsics)).toBe(true);
  });

  test('tolerates keyCode int (single key)', () => {
    const f = parseActionCameraFrame({ ...validFrame, keyCode: 87 });
    expect(f.keyCode).toEqual([87]);
  });

  test('rejects keyCode string', () => {
    expect(() =>
      parseActionCameraFrame({ ...validFrame, keyCode: 'W' as unknown as number }),
    ).toThrow(SchemaValidationError);
  });

  test('rejects missing camera_Follow Offset', () => {
    const bad = { ...validFrame };
    delete (bad as Record<string, unknown>)['camera_Follow Offset'];
    expect(() => parseActionCameraFrame(bad)).toThrow(SchemaValidationError);
  });

  test('detects non-pinhole intrinsics', () => {
    const f = parseActionCameraFrame({
      ...validFrame,
      camera_intrinsics: { fx: 960, fy: 800, cx: 960, cy: 540 },
    });
    expect(isPinhole(f.camera_intrinsics)).toBe(false);
  });

  test('accepts dict-form vectors (PRD canonical format)', () => {
    const f = parseActionCameraFrame({
      ...validFrame,
      camera_position: { x: 1, y: 2, z: 3 },
      camera_rotation_quaternion: { x: 0, y: 0, z: 0, w: 1 },
    });
    expect(f.camera_position).toEqual({ x: 1, y: 2, z: 3 });
    expect(f.camera_rotation_quaternion.w).toBe(1);
  });
});

describe('parseActionCamera (list)', () => {
  test('strict mode throws on first bad frame', () => {
    expect(() => parseActionCamera([{ frame: 0 }], { strict: true })).toThrow(SchemaValidationError);
  });

  test('non-strict mode skips bad frames', () => {
    const frames = parseActionCamera([{ frame: 0 }], { strict: false });
    expect(frames.length).toBe(0);
  });

  test('rejects non-array root', () => {
    expect(() => parseActionCamera({ frame: 0 })).toThrow(SchemaValidationError);
  });
});
