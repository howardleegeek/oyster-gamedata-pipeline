/**
 * Buyer-spec v1 schema — typed interfaces and runtime validators.
 *
 * Source of truth: docs/BUYER_SPEC_V1.md (action_camera 20 fields,
 * systeminfo geometry, depth/*.exr layout).
 *
 * The PRD documents vectors as `{x, y, z}` objects, but the released
 * sample tarball (`samples/buyer-spec-v1-rc1.tar.gz`) emits arrays
 * `[x, y, z]`. The parsers below normalise both into the same TS object.
 */

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------

export class SchemaValidationError extends Error {
  constructor(message: string, public readonly path?: string) {
    super(message);
    this.name = 'SchemaValidationError';
  }
}

// ---------------------------------------------------------------------------
// Vector / quaternion types
// ---------------------------------------------------------------------------

export interface Vector3 {
  x: number;
  y: number;
  z: number;
}

export interface Vector4 {
  x: number;
  y: number;
  z: number;
  w: number;
}

export type Vec3Like = Vector3 | [number, number, number] | readonly [number, number, number];
export type Vec4Like = Vector4 | [number, number, number, number] | readonly [number, number, number, number];

function isNumber(v: unknown): v is number {
  return typeof v === 'number' && Number.isFinite(v);
}

export function parseVector3(src: unknown, fieldName = 'vector'): Vector3 {
  if (Array.isArray(src) && src.length === 3 && src.every(isNumber)) {
    return { x: src[0], y: src[1], z: src[2] };
  }
  if (src !== null && typeof src === 'object') {
    const obj = src as Record<string, unknown>;
    if (isNumber(obj.x) && isNumber(obj.y) && isNumber(obj.z)) {
      return { x: obj.x, y: obj.y, z: obj.z };
    }
  }
  throw new SchemaValidationError(
    `${fieldName}: expected Vector3 ({x,y,z} or [x,y,z]), got ${JSON.stringify(src)}`,
    fieldName,
  );
}

export function parseVector4(src: unknown, fieldName = 'vector'): Vector4 {
  if (Array.isArray(src) && src.length === 4 && src.every(isNumber)) {
    return { x: src[0], y: src[1], z: src[2], w: src[3] };
  }
  if (src !== null && typeof src === 'object') {
    const obj = src as Record<string, unknown>;
    if (isNumber(obj.x) && isNumber(obj.y) && isNumber(obj.z) && isNumber(obj.w)) {
      return { x: obj.x, y: obj.y, z: obj.z, w: obj.w };
    }
  }
  throw new SchemaValidationError(
    `${fieldName}: expected Vector4 ({x,y,z,w} or [x,y,z,w]), got ${JSON.stringify(src)}`,
    fieldName,
  );
}

// ---------------------------------------------------------------------------
// systeminfo.json
// ---------------------------------------------------------------------------

export interface MapBounds {
  min_x: number;
  min_z: number;
  max_x: number;
  max_z: number;
}

export interface Systeminfo {
  /** Process name, e.g. `minecraft.exe`. */
  gameProcessName: string;
  /** Game-window screen origin x. */
  x: number;
  /** Game-window screen origin y. */
  y: number;
  /** Width in pixels (must be 1920 per spec). */
  width: number;
  /** Height in pixels (must be 1080 per spec). */
  height: number;
  /** OS scaling factor (1.0 / 1.5 / 2.0). */
  recordDpi: number;
  /** World:real-world scale ratio. */
  map_scale: number;
  /** 4-corner world bounds. */
  map_bounds: MapBounds;
  /** Any additional fields preserved verbatim. */
  [extra: string]: unknown;
}

function parseMapBounds(src: unknown): MapBounds {
  if (src === null || typeof src !== 'object') {
    throw new SchemaValidationError(`map_bounds: expected object, got ${typeof src}`, 'map_bounds');
  }
  const obj = src as Record<string, unknown>;
  for (const key of ['min_x', 'min_z', 'max_x', 'max_z'] as const) {
    if (!isNumber(obj[key])) {
      throw new SchemaValidationError(`map_bounds.${key}: not a number`, `map_bounds.${key}`);
    }
  }
  return {
    min_x: obj.min_x as number,
    min_z: obj.min_z as number,
    max_x: obj.max_x as number,
    max_z: obj.max_z as number,
  };
}

export function parseSysteminfo(src: unknown): Systeminfo {
  if (src === null || typeof src !== 'object') {
    throw new SchemaValidationError(`systeminfo: expected object, got ${typeof src}`);
  }
  const obj = src as Record<string, unknown>;
  const required = ['gameProcessName', 'x', 'y', 'width', 'height', 'recordDpi', 'map_scale', 'map_bounds'] as const;
  for (const key of required) {
    if (!(key in obj)) {
      throw new SchemaValidationError(`systeminfo: missing field ${key}`, key);
    }
  }
  if (typeof obj.gameProcessName !== 'string') {
    throw new SchemaValidationError('systeminfo.gameProcessName must be string');
  }
  for (const key of ['x', 'y', 'width', 'height', 'recordDpi', 'map_scale'] as const) {
    if (!isNumber(obj[key])) {
      throw new SchemaValidationError(`systeminfo.${key} must be number`);
    }
  }
  const result: Systeminfo = {
    gameProcessName: obj.gameProcessName,
    x: obj.x as number,
    y: obj.y as number,
    width: obj.width as number,
    height: obj.height as number,
    recordDpi: obj.recordDpi as number,
    map_scale: obj.map_scale as number,
    map_bounds: parseMapBounds(obj.map_bounds),
  };
  // Preserve extras
  for (const k of Object.keys(obj)) {
    if (!(required as readonly string[]).includes(k)) {
      result[k] = obj[k];
    }
  }
  return result;
}

// ---------------------------------------------------------------------------
// action_camera.json — 20 fields per frame
// ---------------------------------------------------------------------------

export interface CameraIntrinsics {
  /** Focal length x. Per spec MUST equal fy (pinhole model). */
  fx: number;
  /** Focal length y. */
  fy: number;
  /** Principal-point x. */
  cx: number;
  /** Principal-point y. */
  cy: number;
}

export interface ActionCameraFrame {
  /** 0-indexed frame number. */
  frame: number;
  /** Timestamp `YYYY-MM-DD HH:mm:ss.SSS`. */
  time: string;
  /** Live frame rate. */
  fps: number;
  /** 1=normal, 2=special, 3=loop. */
  route_type: 1 | 2 | 3 | number;
  /** Mouse position normalised [0,1]. */
  mouse_x: number;
  mouse_y: number;
  /** Mouse delta normalised [-1,1]. */
  mouse_dx: number;
  mouse_dy: number;
  /** Virtual-key codes pressed this frame. */
  keyCode: number[];
  camera_position: Vector3;
  /** Euler angles [pitch, yaw, roll] in degrees. */
  camera_rotation_oula: Vector3;
  /** Quaternion in [x, y, z, w] order. */
  camera_rotation_quaternion: Vector4;
  /** Player→camera offset (spec spelling preserved). */
  camera_follow_offset: Vector3;
  camera_intrinsics: CameraIntrinsics;
  /** Per-axis m/s. */
  camera_speed: Vector3;
  player_position: Vector3;
  player_rotation_oula: Vector3;
  player_rotation_quaternion: Vector4;
  player_speed: Vector3;
  /** World:real-world scale ratio. */
  metric_scale: number;
}

function parseCameraIntrinsics(src: unknown): CameraIntrinsics {
  if (src === null || typeof src !== 'object') {
    throw new SchemaValidationError('camera_intrinsics: expected object');
  }
  const obj = src as Record<string, unknown>;
  for (const k of ['fx', 'fy', 'cx', 'cy'] as const) {
    if (!isNumber(obj[k])) {
      throw new SchemaValidationError(`camera_intrinsics.${k} must be number`);
    }
  }
  return {
    fx: obj.fx as number,
    fy: obj.fy as number,
    cx: obj.cx as number,
    cy: obj.cy as number,
  };
}

export function isPinhole(intrinsics: CameraIntrinsics): boolean {
  return intrinsics.fx === intrinsics.fy;
}

export function parseActionCameraFrame(src: unknown): ActionCameraFrame {
  if (src === null || typeof src !== 'object') {
    throw new SchemaValidationError('action_camera frame: expected object');
  }
  const obj = src as Record<string, unknown>;

  // keyCode tolerates int or int[].
  let keyCodeRaw = obj.keyCode ?? [];
  if (typeof keyCodeRaw === 'number') {
    keyCodeRaw = [keyCodeRaw];
  }
  if (!Array.isArray(keyCodeRaw) || !keyCodeRaw.every((k) => typeof k === 'number')) {
    throw new SchemaValidationError(
      `frame ${obj.frame ?? '?'}: keyCode must be int or int[]`,
    );
  }

  if (typeof obj.frame !== 'number') {
    throw new SchemaValidationError('frame: must be number');
  }
  if (typeof obj.time !== 'string') {
    throw new SchemaValidationError(`frame ${obj.frame}: time must be string`);
  }
  for (const key of ['fps', 'route_type', 'mouse_x', 'mouse_y', 'mouse_dx', 'mouse_dy', 'metric_scale'] as const) {
    if (typeof obj[key] !== 'number') {
      throw new SchemaValidationError(`frame ${obj.frame}: ${key} must be number`);
    }
  }
  // The spec key has a space — preserved verbatim in the JSON file.
  const followKey = 'camera_Follow Offset';
  if (!(followKey in obj)) {
    throw new SchemaValidationError(`frame ${obj.frame}: missing 'camera_Follow Offset'`);
  }
  return {
    frame: obj.frame,
    time: obj.time,
    fps: obj.fps as number,
    route_type: obj.route_type as number,
    mouse_x: obj.mouse_x as number,
    mouse_y: obj.mouse_y as number,
    mouse_dx: obj.mouse_dx as number,
    mouse_dy: obj.mouse_dy as number,
    keyCode: (keyCodeRaw as number[]).slice(),
    camera_position: parseVector3(obj.camera_position, 'camera_position'),
    camera_rotation_oula: parseVector3(obj.camera_rotation_oula, 'camera_rotation_oula'),
    camera_rotation_quaternion: parseVector4(obj.camera_rotation_quaternion, 'camera_rotation_quaternion'),
    camera_follow_offset: parseVector3(obj[followKey], followKey),
    camera_intrinsics: parseCameraIntrinsics(obj.camera_intrinsics),
    camera_speed: parseVector3(obj.camera_speed, 'camera_speed'),
    player_position: parseVector3(obj.player_position, 'player_position'),
    player_rotation_oula: parseVector3(obj.player_rotation_oula, 'player_rotation_oula'),
    player_rotation_quaternion: parseVector4(obj.player_rotation_quaternion, 'player_rotation_quaternion'),
    player_speed: parseVector3(obj.player_speed, 'player_speed'),
    metric_scale: obj.metric_scale as number,
  };
}

export function parseActionCamera(src: unknown, opts: { strict?: boolean } = {}): ActionCameraFrame[] {
  const strict = opts.strict ?? true;
  if (!Array.isArray(src)) {
    throw new SchemaValidationError('action_camera: expected JSON array');
  }
  const frames: ActionCameraFrame[] = [];
  for (const item of src) {
    try {
      frames.push(parseActionCameraFrame(item));
    } catch (err) {
      if (strict) throw err;
    }
  }
  return frames;
}

// ---------------------------------------------------------------------------
// gameinfo.xlsx — typed surface for the buyer
// ---------------------------------------------------------------------------

/**
 * Parsed gameinfo.xlsx contents. Operator metadata schema varies by batch,
 * so the SDK exposes a flexible map and a few well-known optional keys.
 */
export interface Gameinfo {
  sheet_name: string;
  columns: string[];
  rows: Array<Record<string, unknown>>;
  /** Convenience: first data row keyed by column header. */
  fields: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// Tarball metadata (catalog)
// ---------------------------------------------------------------------------

/**
 * Catalog-level metadata for a single tarball, returned by the buyer
 * download API.  Includes filename, size, and presigned-URL info.
 */
export interface TarballMetadata {
  /** Vendor-assigned clip identifier. */
  clip_id: string;
  /** Batch id this clip belongs to. */
  batch_id: string;
  /** Vendor identifier. */
  vendor_id: string;
  /** Total bytes of the .tar.gz. */
  size_bytes: number;
  /** SHA-256 of the tarball. */
  sha256: string;
  /** Buyer-spec version, e.g. "v1". */
  spec_version: string;
  /** Original filename of the tarball. */
  filename: string;
  /** Presigned download URL (may be undefined until generated). */
  download_url?: string;
  /** Presigned URL expiry ISO-8601. */
  download_expires_at?: string;
  /** Server-side acceptance status. */
  status: 'pending' | 'accepted' | 'rejected' | 'reviewing';
  /** Optional acceptance report id for cross-reference. */
  report_id?: string;
  /** Server-side ISO-8601 creation timestamp. */
  created_at: string;
}
