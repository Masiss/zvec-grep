#!/usr/bin/env python3
"""Bounded idle fixture for a policy-contained observer diagnostic; not a launcher."""
import json
import pathlib
import sys
import threading
import time

ROOT = pathlib.Path('/home/masis/src/zvec-grep/research/m4-native/.work/m4-native-workspace-quota')
OPERATION = 'm4-observer-diagnostic-20261028t065055z'


def main() -> int:
    if len(sys.argv) != 2:
        raise ValueError('one operation-owned workspace is required')
    workspace = pathlib.Path(sys.argv[1])
    expected = ROOT / OPERATION / 'diagnostic' / 'workspace'
    if workspace != expected or workspace.resolve(strict=True) != expected or workspace.is_symlink():
        raise ValueError('unexpected diagnostic workspace')
    for name in ('start', 'end'):
        if name == 'end':
            threading.Event().wait(8.0)
        print(json.dumps({'event': name, 'monotonic_ns': time.monotonic_ns(),
                          'wall_time_ns': time.time_ns()}), flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
