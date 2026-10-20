// Offline suite, isolated per file. Python must have the repository installed.
import {readdirSync} from 'node:fs';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';

if (Number(process.versions.node.split('.')[0]) !== 24) {
  throw Error('Worker regressions require Node 24 (node:sqlite)');
}
const directory = new URL('./', import.meta.url);
const excluded = new Set([
  'test-db.mjs', // Shared SQLite helper, not a standalone test.
  'test_stage_a_stabilization.mjs', // Built/tested by tests/test_stage_a_stabilization.py.
]);
const files = readdirSync(directory).filter(name =>
  name.startsWith('test') && name.endsWith('.mjs') && !excluded.has(name)).sort();
if (!files.length) throw Error('Worker regression inventory is empty');
console.log(`Offline Worker suite: ${files.length} standalone files`);
const result = spawnSync(process.execPath, [
  '--test', '--test-concurrency=1', ...files.map(name => fileURLToPath(new URL(name, directory))),
], {
  cwd: fileURLToPath(new URL('../', directory)),
  env: {...process.env, PYTHONUTF8: '1'},
  stdio: 'inherit',
});
if (result.error) throw result.error;
process.exit(result.status ?? 1);
