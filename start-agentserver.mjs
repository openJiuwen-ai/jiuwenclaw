#!/usr/bin/env node
/**
 * jiuwenclaw-agentserver 启动脚本 (Node.js 版本)
 * 通过 Node.js child_process 启动
 */

import { spawn } from 'child_process';
import path from 'path';
import fs from 'fs';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const SCRIPT_DIR = path.dirname(__filename);
const PORT = process.argv[2] || '18092';

const env = {
  ...process.env,
  LD_LIBRARY_PATH: '/storage/Users/currentUser/usr/local/lib:/data/service/hnp/libxml2.org/libxml2_2.11.9/lib:/data/service/hnp/libxslt.org/libxslt_1.1.39/lib',
  PATH: '/storage/Users/currentUser/.harmonybrew/bin:/storage/Users/currentUser/usr/rust-1.95.0-aarch64-unknown-linux-ohos/bin:/storage/Users/currentUser/.cargo/bin:/storage/Users/currentUser/.local/bin:/storage/Users/currentUser/usr/local/bin:/data/service/hnp/bin:/usr/bin:/vendor/bin:' + process.env.PATH,
  RUST_HOME: '/storage/Users/currentUser/usr/rust-1.95.0-aarch64-unknown-linux-ohos',
  CARGO_HOME: '/storage/Users/currentUser/.cargo',
  CARGO_TARGET_AARCH64_UNKNOWN_LINUX_OHOS_LINKER: 'clang',
  TMPDIR: process.env.TMPDIR || '/storage/Users/currentUser/tmp',
  SSL_CERT_FILE: process.env.SSL_CERT_FILE || '/etc/ssl/certs/cacert.pem',
  OHOS_BINARY_SIGN_TOOL: '/storage/Users/currentUser/usr/rust-1.95.0-aarch64-unknown-linux-ohos/tool/binary-sign-tool',
  CC: 'clang',
  CXX: 'clang++',
  HNP_PUBLIC_HOME: '/data/service/hnp',
  HNP_PRIVATE_HOME: '/data/app',
};

// 确保 tmp 目录存在
if (!fs.existsSync(env.TMPDIR)) {
  fs.mkdirSync(env.TMPDIR, { recursive: true });
}

console.log(`Starting jiuwenclaw-agentserver on port ${PORT}...`);
console.log(`LD_LIBRARY_PATH=${env.LD_LIBRARY_PATH}`);

const pythonPath = path.join(SCRIPT_DIR, '.venv', 'bin', 'python');
const args = ['-m', 'jiuwenclaw.app_agentserver', '--port', PORT];

const child = spawn(pythonPath, args, {
  cwd: SCRIPT_DIR,
  env,
  stdio: 'inherit',
});

child.on('exit', (code) => {
  process.exit(code);
});

child.on('error', (err) => {
  console.error('Failed to start:', err.message);
  process.exit(1);
});
