#!/usr/bin/env node
/**
 * Mineflayer bot subprocess for the Oyster L4 Minecraft trajectory pipeline (Phase 1).
 *
 * Communicates with a Python coordinator over stdin / stdout using the
 * line-delimited JSON protocol documented in ./protocol.md.
 *
 * Hard rules:
 *   - stdout is RESERVED for protocol messages. NOTHING else writes to it.
 *     Diagnostic output goes to stderr.
 *   - Every observation MUST include the latest bot.position + inventory
 *     so the Python side can persist metadata.jsonl unconditionally.
 *   - Unknown action ops MUST reply with {ok: false, error: "unknown_op"} —
 *     never silently dropped.
 *   - Unhandled exceptions MUST emit {type: "error", fatal: true} on stdout
 *     before the process exits, otherwise the coordinator hangs.
 */

'use strict';

const readline = require('node:readline');
const process = require('node:process');

const PROTOCOL_VERSION = 1;

// ---- args ------------------------------------------------------------------
//
// We parse a tiny subset of CLI args ourselves to avoid pulling another
// dependency. The Python coordinator passes:
//
//   --host <h> --port <p> --username <u> [--version <v>]
//
// All optional, with sensible defaults for `localhost:25565` + a stable
// username.

function parseArgs(argv) {
  const opts = {
    host: 'localhost',
    port: 25565,
    username: 'oyster_bot',
    version: false, // mineflayer auto-detect
  };
  for (let i = 2; i < argv.length; i++) {
    const flag = argv[i];
    const next = argv[i + 1];
    if (flag === '--host' && next) {
      opts.host = next;
      i++;
    } else if (flag === '--port' && next) {
      opts.port = parseInt(next, 10);
      if (Number.isNaN(opts.port)) {
        throw new Error(`invalid --port: ${next}`);
      }
      i++;
    } else if (flag === '--username' && next) {
      opts.username = next;
      i++;
    } else if (flag === '--version' && next) {
      opts.version = next;
      i++;
    } else if (flag === '--help' || flag === '-h') {
      process.stderr.write(
        'Usage: bot.js --host <h> --port <p> --username <u> [--version <v>]\n',
      );
      process.exit(0);
    }
    // Unknown flags ignored — keeps forward-compat with future coordinator
    // args that this version of the bot doesn't yet understand.
  }
  return opts;
}

// ---- stdout writer ---------------------------------------------------------

/**
 * Send one protocol message. We always set v=PROTOCOL_VERSION here so callers
 * don't have to remember.
 */
function send(message) {
  const wire = { v: PROTOCOL_VERSION, ...message };
  // JSON.stringify will throw on circular refs — let it; the global error
  // handler will catch and emit a fatal error before exit.
  const line = JSON.stringify(wire);
  // Single write call so two concurrent send() calls can't interleave.
  process.stdout.write(line + '\n');
}

function logErr(...args) {
  process.stderr.write(args.join(' ') + '\n');
}

// ---- bot state -------------------------------------------------------------

let bot = null;
let pathfinder = null;
let goalsModule = null;
let MovementsModule = null;
let connected = false;
let spawned = false;
let shuttingDown = false;

/**
 * Build the JSON-friendly snapshot of bot.position / inventory / etc that we
 * include in every observation. Defensive — bot fields can be undefined
 * during connection / disconnection edge cases.
 */
function snapshotBot() {
  if (!bot || !bot.entity) {
    return null;
  }
  const p = bot.entity.position;
  return {
    position: p ? [p.x, p.y, p.z] : null,
    yaw: bot.entity.yaw ?? null,
    pitch: bot.entity.pitch ?? null,
    health: bot.health ?? null,
    food: bot.food ?? null,
    xp: bot.experience ? bot.experience.points : 0,
  };
}

function snapshotInventory() {
  if (!bot || !bot.inventory) return [];
  // bot.inventory.slots is an array indexed by slot number; many slots are null.
  // We surface only non-null entries to keep the JSON line readable.
  const out = [];
  const slots = bot.inventory.slots || [];
  for (let i = 0; i < slots.length; i++) {
    const s = slots[i];
    if (s && s.name) {
      out.push({ slot: i, name: s.name, count: s.count ?? 1 });
    }
  }
  return out;
}

function snapshotBlocksNear(radius) {
  if (!bot || !bot.entity || !bot.findBlocks) return [];
  // findBlocks returns an array of Vec3 positions. We do a small radius (8)
  // and cap at 32 hits so the JSON line stays small.
  const positions = bot.findBlocks({
    matching: () => true,
    maxDistance: radius,
    count: 32,
  });
  return positions.map((pos) => {
    const block = bot.blockAt(pos);
    return {
      pos: [pos.x, pos.y, pos.z],
      name: block && block.name ? block.name : 'unknown',
    };
  });
}

function snapshotEntitiesNear(radius) {
  if (!bot || !bot.entities || !bot.entity) return [];
  const out = [];
  const me = bot.entity.position;
  for (const id of Object.keys(bot.entities)) {
    const e = bot.entities[id];
    if (!e || e === bot.entity) continue;
    const ep = e.position;
    if (!ep || !me) continue;
    const dx = ep.x - me.x;
    const dy = ep.y - me.y;
    const dz = ep.z - me.z;
    const distance = Math.sqrt(dx * dx + dy * dy + dz * dz);
    if (distance > radius) continue;
    out.push({
      id: e.id,
      type: e.name || e.kind || 'entity',
      pos: [ep.x, ep.y, ep.z],
      distance,
    });
  }
  // Cap to keep the line readable.
  return out.slice(0, 16);
}

function buildObservation(id, ok, error) {
  return {
    type: 'observation',
    id,
    ok,
    error: error ?? null,
    tick: bot && typeof bot.time?.age === 'number' ? bot.time.age : -1,
    bot: snapshotBot(),
    inventory: snapshotInventory(),
    blocks_near: snapshotBlocksNear(8),
    entities_near: snapshotEntitiesNear(16),
    task_state: null, // Phase 3 fills this in
  };
}

// ---- mineflayer wiring ----------------------------------------------------

function lazyLoadMineflayer() {
  // Load lazily so the JSON-line protocol parsing in tests (which never call
  // connect()) doesn't need the npm install. The parent test harness can
  // unit-test this file's parseArgs / snapshot helpers without mineflayer.
  /* eslint-disable global-require */
  const mineflayer = require('mineflayer');
  pathfinder = require('mineflayer-pathfinder').pathfinder;
  goalsModule = require('mineflayer-pathfinder').goals;
  MovementsModule = require('mineflayer-pathfinder').Movements;
  /* eslint-enable global-require */
  return mineflayer;
}

function connect(opts) {
  let mineflayer;
  try {
    mineflayer = lazyLoadMineflayer();
  } catch (err) {
    send({
      type: 'error',
      fatal: true,
      error: `failed to require mineflayer: ${err.message}. Did you run \`npm install\` in mineflayer/?`,
    });
    process.exit(1);
  }

  bot = mineflayer.createBot({
    host: opts.host,
    port: opts.port,
    username: opts.username,
    version: opts.version || false,
    auth: 'offline', // local server, online-mode=false
  });

  bot.loadPlugin(pathfinder);

  bot.once('login', () => {
    connected = true;
    logErr('[bot] login OK');
  });

  bot.once('spawn', () => {
    spawned = true;
    // Configure pathfinder movements once the world is loaded enough that we
    // know which mc-data to use.
    try {
      const mcData = require('minecraft-data')(bot.version);
      const movements = new MovementsModule(bot, mcData);
      bot.pathfinder.setMovements(movements);
    } catch (err) {
      logErr('[bot] pathfinder setup warning:', err.message);
    }
    const snap = snapshotBot() || {};
    send({
      type: 'spawn',
      position: snap.position,
      yaw: snap.yaw,
      pitch: snap.pitch,
      health: snap.health,
      food: snap.food,
      xp: snap.xp,
      game_mode: bot.game?.gameMode ?? 'unknown',
      dimension: bot.game?.dimension ?? 'overworld',
      world_seed: null, // server doesn't expose this to the client by default
    });
  });

  bot.on('error', (err) => {
    if (shuttingDown) return;
    send({ type: 'error', fatal: false, error: String(err && err.message ? err.message : err) });
  });

  bot.on('kicked', (reason) => {
    send({ type: 'error', fatal: true, error: `kicked: ${reason}` });
  });

  bot.on('end', (reason) => {
    if (shuttingDown) return;
    send({ type: 'error', fatal: true, error: `disconnected: ${reason ?? 'unknown'}` });
    process.exit(0);
  });
}

// ---- action dispatch ------------------------------------------------------

async function dispatchAction(id, action) {
  if (!spawned) {
    send(buildObservation(id, false, 'bot_not_spawned'));
    return;
  }
  const op = (action && action.op) || '';
  try {
    switch (op) {
      case 'noop':
        // Wait one in-game tick before replying so callers can pace the loop.
        await new Promise((resolve) => setTimeout(resolve, 50));
        send(buildObservation(id, true, null));
        break;

      case 'move_to': {
        const t = action.target;
        if (!Array.isArray(t) || t.length !== 3) {
          send(buildObservation(id, false, 'move_to.target must be [x,y,z]'));
          break;
        }
        const goal = new goalsModule.GoalBlock(t[0], t[1], t[2]);
        await bot.pathfinder.goto(goal);
        send(buildObservation(id, true, null));
        break;
      }

      case 'dig': {
        const t = action.target;
        if (!Array.isArray(t) || t.length !== 3) {
          send(buildObservation(id, false, 'dig.target must be [x,y,z]'));
          break;
        }
        const Vec3 = require('vec3');
        const block = bot.blockAt(new Vec3(t[0], t[1], t[2]));
        if (!block) {
          send(buildObservation(id, false, 'no block at target'));
          break;
        }
        if (!bot.canDigBlock(block)) {
          send(buildObservation(id, false, 'cannot dig block'));
          break;
        }
        await bot.dig(block);
        send(buildObservation(id, true, null));
        break;
      }

      case 'look': {
        const yaw = Number(action.yaw);
        const pitch = Number(action.pitch ?? 0);
        if (!Number.isFinite(yaw) || !Number.isFinite(pitch)) {
          send(buildObservation(id, false, 'look.yaw/pitch must be finite numbers'));
          break;
        }
        await bot.look(yaw, pitch, true);
        send(buildObservation(id, true, null));
        break;
      }

      case 'chat': {
        const message = String(action.message ?? '');
        if (!message) {
          send(buildObservation(id, false, 'chat.message must be non-empty'));
          break;
        }
        bot.chat(message);
        // Single tick so the message hits the server before we snapshot.
        await new Promise((resolve) => setTimeout(resolve, 50));
        send(buildObservation(id, true, null));
        break;
      }

      default:
        send(buildObservation(id, false, 'unknown_op'));
    }
  } catch (err) {
    send(buildObservation(id, false, `${op || 'action'}_error: ${err.message || err}`));
  }
}

// ---- stdin loop -----------------------------------------------------------

function handleParentMessage(line) {
  let msg;
  try {
    msg = JSON.parse(line);
  } catch (err) {
    logErr('[bot] received non-JSON from parent, ignoring:', line.slice(0, 100));
    return;
  }
  if (!msg || typeof msg !== 'object') return;
  switch (msg.type) {
    case 'hello':
      send({
        type: 'hello_ack',
        bot: bot && bot.username ? bot.username : null,
        // mineflayer version comes from the package; it's safe to read at runtime.
        mineflayer_version: (() => {
          try {
            return require('mineflayer/package.json').version;
          } catch (_) {
            return null;
          }
        })(),
      });
      break;

    case 'action':
      if (typeof msg.id !== 'number') {
        logErr('[bot] action message missing numeric id, ignoring');
        return;
      }
      dispatchAction(msg.id, msg.action || {});
      break;

    case 'shutdown':
      shuttingDown = true;
      try {
        if (bot && bot.quit) bot.quit('shutdown');
      } catch (_) {
        /* ignore */
      }
      send({ type: 'goodbye' });
      // Give stdout a tick to flush before exit.
      setTimeout(() => process.exit(0), 50);
      break;

    default:
      logErr('[bot] unknown parent message type:', msg.type);
  }
}

// ---- main -----------------------------------------------------------------

function main() {
  const opts = parseArgs(process.argv);

  process.on('uncaughtException', (err) => {
    try {
      send({
        type: 'error',
        fatal: true,
        error: `uncaught: ${err.message || err}`,
      });
    } catch (_) {
      /* nothing useful to do; we're exiting anyway */
    }
    process.exit(1);
  });

  process.on('unhandledRejection', (reason) => {
    try {
      send({
        type: 'error',
        fatal: true,
        error: `unhandledRejection: ${reason && reason.message ? reason.message : reason}`,
      });
    } catch (_) {
      /* nothing useful to do */
    }
    process.exit(1);
  });

  process.stdin.setEncoding('utf8');
  const rl = readline.createInterface({
    input: process.stdin,
    output: null,
    terminal: false,
    crlfDelay: Infinity,
  });
  rl.on('line', handleParentMessage);
  rl.on('close', () => {
    // Parent closed our stdin → graceful shutdown.
    if (!shuttingDown) {
      shuttingDown = true;
      try {
        if (bot && bot.quit) bot.quit('parent_closed_stdin');
      } catch (_) {
        /* ignore */
      }
      setTimeout(() => process.exit(0), 50);
    }
  });

  connect(opts);
}

// Only run main() when invoked directly — not when this file is required by
// a test harness that wants to unit-test individual helpers.
if (require.main === module) {
  main();
}

module.exports = {
  parseArgs,
  buildObservation,
  PROTOCOL_VERSION,
};
