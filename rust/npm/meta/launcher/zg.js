#!/usr/bin/env node
"use strict";

const { spawnSync } = require("node:child_process");
const { statSync } = require("node:fs");
const { join } = require("node:path");
const { ensureInstalled } = require("../install.cjs");

try {
  const metaRoot = join(__dirname, "..");
  let native;
  const silentUpgrade = { run: () => ({ status: 0, stdout: "", stderr: "" }) };
  const originalLog = console.log;
  const originalWarn = console.warn;
  console.log = () => {};
  console.warn = () => {};
  try {
    ensureInstalled(metaRoot, false, silentUpgrade);
    native = join(metaRoot, "bin", "zg.exe");
    // A blocked npm lifecycle script leaves the packaged placeholder in place.
    // Retry materialization rather than executing it accidentally.
    if (statSync(native).size < 4096) ensureInstalled(metaRoot, true, silentUpgrade);
  } finally {
    console.log = originalLog;
    console.warn = originalWarn;
  }
  const result = spawnSync(native, process.argv.slice(2), { stdio: "inherit", env: process.env });
  if (result.error) throw result.error;
  if (result.signal) process.kill(process.pid, result.signal);
  else process.exitCode = result.status === null ? 1 : result.status;
} catch (error) {
  console.error(`zvec-grep: ${error.message}`);
  process.exitCode = 1;
}
