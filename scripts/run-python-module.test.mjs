import assert from 'node:assert/strict';
import path from 'node:path';
import test from 'node:test';
import { pythonCommands, runPythonModule } from './run-python-module.mjs';

const root = path.resolve('test repository');
const defaults = { env: {}, platform: 'linux', root, exists: () => false };

test('explicit Python overrides active and project environments as one literal executable', () => {
  assert.deepEqual(pythonCommands({
    ...defaults, env: { PYTHON: '/custom path/python', VIRTUAL_ENV: '/active' },
    exists: () => true,
  }), [['/custom path/python']]);
});

test('active environment precedes project environments on Windows and Unix', () => {
  for (const platform of ['win32', 'linux']) {
    assert.deepEqual(pythonCommands({
      ...defaults, platform, env: { VIRTUAL_ENV: '/active' }, exists: () => true,
    }), [[path.join('/active', platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')]]);
  }
});

test('project root and backend virtual environments precede PATH discovery', () => {
  const primary = path.join(root, '.venv/bin/python');
  const backend = path.join(root, 'backend/.venv/bin/python');
  assert.deepEqual(pythonCommands({ ...defaults, exists: () => true }), [[primary]]);
  assert.deepEqual(pythonCommands({ ...defaults, exists: (file) => file === backend }), [[backend]]);
});

test('only missing PATH commands fall through, including Windows py launcher prefix', () => {
  const calls = [];
  const status = runPythonModule(['pytest', '-q'], {
    ...defaults, platform: 'win32', report: assert.fail,
    spawn: (command, args) => {
      calls.push([command, args]);
      return command === 'py' ? { status: 0 } : { error: { code: 'ENOENT' } };
    },
  });
  assert.equal(status, 0);
  assert.deepEqual(calls, [
    ['python', ['-m', 'pytest', '-q']],
    ['python3', ['-m', 'pytest', '-q']],
    ['py', ['-3', '-m', 'pytest', '-q']],
  ]);
});

test('arguments remain literal and child failure stops discovery', () => {
  let calls = 0;
  const env = { PYTHON: '/custom path/python' };
  assert.equal(runPythonModule(['pytest', 'file with spaces.py', '$(literal);&'], {
    ...defaults, env, report: assert.fail,
    spawn: (command, args, options) => {
      calls++;
      assert.equal(command, env.PYTHON);
      assert.deepEqual(args, ['-m', 'pytest', 'file with spaces.py', '$(literal);&']);
      assert.deepEqual(options, { cwd: root, env, stdio: 'inherit', shell: false });
      return { status: 7 };
    },
  }), 7);
  assert.equal(calls, 1);
});

test('selected environments and non-ENOENT errors fail without fallback', () => {
  for (const options of [
    { env: { PYTHON: '/missing/python' } },
    { env: { VIRTUAL_ENV: '/missing' } },
    { exists: () => true },
    { errorCode: 'EACCES' },
  ]) {
    let calls = 0;
    assert.equal(runPythonModule(['pytest'], {
      ...defaults, ...options, report: () => {},
      spawn: () => { calls++; return { error: { code: options.errorCode ?? 'ENOENT', message: 'failure' } }; },
    }), 1);
    assert.equal(calls, 1);
  }
});

test('an installed PATH interpreter with a failing module does not try another Python', () => {
  let calls = 0;
  assert.equal(runPythonModule(['pytest'], {
    ...defaults, report: assert.fail,
    spawn: () => { calls++; return { status: 5 }; },
  }), 5);
  assert.equal(calls, 1);
});

test('signals, exhausted discovery, and missing module return failure', () => {
  assert.equal(runPythonModule(['pytest'], { ...defaults, report: () => {}, spawn: () => ({ signal: 'SIGTERM', status: null }) }), 1);
  assert.equal(runPythonModule(['pytest'], { ...defaults, report: () => {}, spawn: () => ({ error: { code: 'ENOENT' } }) }), 1);
  assert.equal(runPythonModule([], { ...defaults, report: () => {}, spawn: assert.fail }), 1);
});
