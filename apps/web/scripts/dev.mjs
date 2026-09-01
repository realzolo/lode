import { spawn } from 'node:child_process';
import { writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const args = process.argv.slice(2);
const port = resolvePort(args);

// Next development servers cannot safely share a build directory. Derive one
// from the port so parallel local sessions do not corrupt each other's chunks.
if (!process.env.LODE_NEXT_DIST_DIR) {
  process.env.LODE_NEXT_DIST_DIR = `.next-dev-${port}`;
}

if (!process.env.LODE_NEXT_TSCONFIG) {
  const devTsconfig = `.next-dev-${port}.tsconfig.json`;
  const devDistTypes = `.next-dev-${port}/types/**/*.ts`;
  writeFileSync(devTsconfig, `${JSON.stringify({
    extends: './tsconfig.json',
    compilerOptions: {
      plugins: [{ name: 'next' }],
    },
    include: ['next-env.d.ts', '**/*.ts', '**/*.tsx', '.next/types/**/*.ts', devDistTypes],
  }, null, 2)}\n`);
  process.env.LODE_NEXT_TSCONFIG = devTsconfig;
}

const nextCli = fileURLToPath(new URL('../node_modules/next/dist/bin/next', import.meta.url));
const child = spawn(process.execPath, [nextCli, 'dev', ...args], {
  env: process.env,
  stdio: 'inherit',
});

child.on('exit', (code, signal) => {
  if (signal) process.kill(process.pid, signal);
  process.exit(code ?? 1);
});

function resolvePort(cliArgs) {
  const portOption = cliArgs.find((arg) => arg.startsWith('--port='));
  if (portOption) return portOption.slice('--port='.length);

  const shortIndex = cliArgs.indexOf('-p');
  const longIndex = cliArgs.indexOf('--port');
  const value = shortIndex >= 0 ? cliArgs[shortIndex + 1] : longIndex >= 0 ? cliArgs[longIndex + 1] : process.env.PORT;

  return value && /^\d+$/.test(value) ? value : '3000';
}
