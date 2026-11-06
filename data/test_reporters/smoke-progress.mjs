// Standalone script lifecycle reporter. No framework, network, or repo imports.
import { openSync, writeSync, closeSync, realpathSync } from 'node:fs';
import { isMainThread } from 'node:worker_threads';

const MAX_RECORD_BYTES = 16384;
const MAX_SPOOL_BYTES = 65536;
let fd, seq = 0, size = 0, disabled = false;

function warning(code) {
  try { writeSync(2, `[test-run] ${code}\n`); }
  catch { /* No usable telemetry or stderr; preserve the script's exit. */ }
}

function emit(type, payload) {
  if (fd === undefined || disabled) return;
  try {
    const bytes = Buffer.from(JSON.stringify({ schema: 1, seq: seq + 1, type, payload }) + '\n');
    if (bytes.length > MAX_RECORD_BYTES || size + bytes.length > MAX_SPOOL_BYTES)
      throw new Error('bounded sidecar');
    if (writeSync(fd, bytes) !== bytes.length) throw new Error('short write');
    size += bytes.length;
    seq++;
  } catch {
    disabled = true;
    warning('node_reporter_write_failed_progress_incomplete');
  }
}

// An optional liveness checkpoint, never an assertion or completed unit.
export function checkpoint() { emit('heartbeat', { output: false }); }

// Workers inherit --import/execArgv and env. Only the selected main script
// owns this sidecar. No assertion total, skip or nested-script inference.
if (isMainThread && (!process.env.CLAYRUNE_SMOKE_PROGRESS_OWNER_PID ||
    process.env.CLAYRUNE_SMOKE_PROGRESS_OWNER_PID === String(process.pid))) {
  // Forked Node children inherit --import and env but must not append here.
  process.env.CLAYRUNE_SMOKE_PROGRESS_OWNER_PID = String(process.pid);
  let manifest;
  let valid = false;
  try {
    manifest = JSON.parse(process.env.CLAYRUNE_SMOKE_PROGRESS_MANIFEST || '');
    if (!manifest || Object.keys(manifest).sort().join(',') !== 'schema,script,unit_id' ||
        manifest.schema !== 1 || typeof manifest.script !== 'string' ||
        typeof manifest.unit_id !== 'string' || !manifest.unit_id || manifest.unit_id.length > 1024 ||
        !process.argv[1] || realpathSync.native(process.argv[1]) !== manifest.script)
      throw new Error('manifest mismatch');
    valid = true;
  } catch {
    warning('node_reporter_manifest_invalid_progress_incomplete');
  }
  if (valid) {
    try {
      // Existing exclusive launcher-created file only; never create a path.
      fd = openSync(process.env.CLAYRUNE_SMOKE_PROGRESS_PATH, 'r+');
    } catch {
      warning('node_reporter_sidecar_unavailable_progress_incomplete');
    }
    emit('run_started', { reporter_version: 'node-script-1' });
    emit('collection', { revision: 1, selected_total: 1 });
    emit('unit_started', { unit_id: manifest.unit_id, attempt: 1 });
    process.once('exit', code => {
      emit('run_finished', { state: code === 0 ? 'passed' : 'failed', exit_code: code, not_run: 0 });
      if (fd !== undefined) {
        try { closeSync(fd); }
        catch { warning('node_reporter_close_failed_progress_incomplete'); }
      }
    });
  }
}
