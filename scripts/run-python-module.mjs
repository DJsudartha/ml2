import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const repositoryRoot = fileURLToPath(new URL('../', import.meta.url));

export function pythonCommands({ env, platform, root, exists = existsSync }) {
  const venvPython = (directory) => path.join(
    directory, platform === 'win32' ? 'Scripts' : 'bin',
    platform === 'win32' ? 'python.exe' : 'python',
  );
  if (env.PYTHON) return [[env.PYTHON]];
  if (env.VIRTUAL_ENV) return [[venvPython(env.VIRTUAL_ENV)]];
  for (const directory of ['.venv', 'backend/.venv']) {
    const executable = venvPython(path.join(root, directory));
    if (exists(executable)) return [[executable]];
  }
  return platform === 'win32'
    ? [['python'], ['python3'], ['py', '-3']]
    : [['python'], ['python3']];
}

export function runPythonModule(args, {
  env = process.env,
  platform = process.platform,
  root = repositoryRoot,
  exists = existsSync,
  spawn = spawnSync,
  report = console.error,
} = {}) {
  if (!args.length) {
    report('Usage: node scripts/run-python-module.mjs <module> [arguments...]');
    return 1;
  }
  const commands = pythonCommands({ env, platform, root, exists });
  const authoritative = Boolean(env.PYTHON || env.VIRTUAL_ENV || commands[0][0] !== 'python');
  for (const [command, ...prefix] of commands) {
    const result = spawn(command, [...prefix, '-m', ...args], {
      cwd: root, env, stdio: 'inherit', shell: false,
    });
    if (result.error) {
      if (!authoritative && result.error.code === 'ENOENT') continue;
      report(`Cannot run Python executable ${command}: ${result.error.message}`);
      return 1;
    }
    if (result.signal) report(`Python exited after signal ${result.signal}`);
    return result.status ?? 1;
  }
  report('No Python executable found. Set PYTHON to an executable path or activate a virtual environment.');
  return 1;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.exitCode = runPythonModule(process.argv.slice(2));
}
