#!/usr/bin/env python3
"""Fixture worker for the qgroup diagnostic. Not a standalone launcher.

The policy-bound controller must create the operation directory, supply its
workspace and enforce all resource/telemetry/cleanup controls. This worker does
not read quota counters, start a monitor, or launch benchmark arms.
"""
from __future__ import annotations

import json
import os
import pathlib
import signal
import sys
import threading
import time

FIXTURE_BYTES = 16 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024
FIXTURE_HOLD_SECONDS = 3
OBSERVATION_SECONDS = 40
RESOURCE_ROOT = pathlib.Path('/home/masis/src/zvec-grep/research/m4-native/.work/m4-native-workspace-quota')


def event(name: str) -> None:
    print(json.dumps({'event': name, 'monotonic_ns': time.monotonic_ns(),
                      'wall_time_ns': time.time_ns()}, separators=(',', ':')), flush=True)


def run_fixture(workspace: pathlib.Path, size: int = FIXTURE_BYTES,
                hold_seconds: int = FIXTURE_HOLD_SECONDS) -> None:
    if size < 0 or size > FIXTURE_BYTES or hold_seconds < 0 or hold_seconds > FIXTURE_HOLD_SECONDS:
        raise ValueError('fixture size or hold exceeds diagnostic limit')
    # The caller owns the directory. dir_fd prevents path traversal after opening it.
    dfd = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    fd = None
    created = False
    try:
        event('create_before')
        fd = os.open('fixture.bin', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o600, dir_fd=dfd)
        created = True
        event('create_after')
        event('write_before')
        remaining = size
        while remaining:
            chunk = os.urandom(min(CHUNK_BYTES, remaining))
            view = memoryview(chunk)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError('short fixture write')
                view = view[written:]
                remaining -= written
        event('write_after')
        event('close_before')
        os.close(fd)
        fd = None
        event('close_after')
        # Fixed hold, not conditioned on quota readings; gives the watchdog a
        # chance to capture the allocated fixture before unlink.
        threading.Event().wait(hold_seconds)
        event('unlink_before')
        os.unlink('fixture.bin', dir_fd=dfd)
        created = False
        event('unlink_after')
    finally:
        if fd is not None:
            os.close(fd)
        if created:
            os.unlink('fixture.bin', dir_fd=dfd)
        os.close(dfd)


def main() -> int:
    if len(sys.argv) != 2:
        raise ValueError('expected one diagnostic workspace path')
    workspace = pathlib.Path(sys.argv[1])
    resolved = workspace.resolve(strict=True)
    if (workspace.is_symlink() or workspace != resolved or resolved.name != 'workspace' or
            resolved.parent.name != 'diagnostic' or
            not resolved.parent.parent.name.startswith('m4-qgroup-diagnostic-') or
            resolved.parent.parent.parent != RESOURCE_ROOT):
        raise ValueError('workspace is not a canonical diagnostic-owned workspace')
    # SIGTERM unwinds so the operation-owned fixture is removed on watchdog stop.
    def terminate(_signum, _frame):
        raise SystemExit('diagnostic terminated')
    signal.signal(signal.SIGTERM, terminate)
    run_fixture(resolved)
    event('observation_start')
    # Wait through a fixed observation window; quota samples belong to the watchdog.
    deadline = time.monotonic() + OBSERVATION_SECONDS
    threading.Event().wait(max(0, deadline - time.monotonic()))
    event('observation_end')
    return 0


if __name__ == '__main__':
    sys.exit(main())
