#!/usr/bin/env python3
"""Refresh the M4 workspace attestation without modifying the workspace or quota.

Run as root only after reviewing this file. No benchmark or preflight is launched.
"""
import datetime
import json
import os
import pathlib
import pwd
import re
import stat
import subprocess
import tempfile

WORKSPACE = '/home/masis/src/zvec-grep/research/m4-native/.work/m4-native-workspace-quota'
# Fixed policy path in sticky /var/tmp; replacement uses a root-owned secure tempfile.
ATTESTATION = pathlib.Path('/var') / 'tmp' / 'm4-native-workspace-quota-pwd.attestation.json'
UUID = '6c15377e-2602-4870-96ba-50e58605309c'
CAP = 402653184
RESERVE = 67108864


def command(*args):
    return subprocess.run(args, check=True, text=True, capture_output=True, timeout=10).stdout


def validate(fs, rootid, status, qgroups):
    if fs.split() != ['btrfs', UUID, '/'] or rootid.strip() != '259':
        raise ValueError('filesystem or subvolume identity mismatch')
    for pattern in (r'^\s*Enabled:\s+yes\s*$', r'^\s*Mode:\s+squota(?:\s|$)',
                    r'^\s*Inconsistent:\s+no\s*$', r'^\s*Override limits:\s+no\s*$'):
        if not re.search(pattern, status, re.MULTILINE | re.IGNORECASE):
            raise ValueError('quota status is not enabled, consistent, enforced squota')
    rows = [line.split() for line in qgroups.splitlines() if line.split() and line.split()[0] == '0/259']
    if len(rows) != 1 or len(rows[0]) < 4:
        raise ValueError('missing or ambiguous qgroup')
    try:
        referenced, exclusive, cap = map(int, rows[0][1:4])
    except ValueError as exc:
        raise ValueError('invalid qgroup counters') from exc
    if cap != CAP or referenced < 0 or exclusive < 0 or referenced > cap or cap - referenced < RESERVE:
        raise ValueError('qgroup cap or headroom does not meet the existing policy')
    return referenced


def verify():
    p = pathlib.Path(WORKSPACE)
    if p.is_symlink() or str(p.resolve(strict=True)) != WORKSPACE:
        raise ValueError('workspace path is not the expected canonical path')
    info = p.stat()
    if info.st_uid != pwd.getpwnam('masis').pw_uid or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError('unexpected workspace owner or permissions')
    return validate(command('findmnt', '-n', '-o', 'FSTYPE,UUID,TARGET', '-T', WORKSPACE),
                    command('btrfs', 'inspect-internal', 'rootid', WORKSPACE),
                    command('btrfs', 'quota', 'status', '/'),
                    command('btrfs', 'qgroup', 'show', '--raw', '-r', WORKSPACE))


def refresh():
    if os.geteuid() != 0:
        raise PermissionError('must run as root')
    info = ATTESTATION.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_gid != 0 or stat.S_IMODE(info.st_mode) != 0o644:
        raise ValueError('existing attestation is not a root-owned regular file with mode 0644')
    verify()
    payload = {'schema_version': 1, 'filesystem_uuid': UUID, 'mountpoint': '/',
               'workspace_subvolume': WORKSPACE, 'subvolume_id': 259, 'qgroup_id': '0/259',
               'max_referenced_bytes': CAP, 'accounting_mode': 'squota',
               'verified_at_utc': datetime.datetime.now(datetime.UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}
    fd, name = tempfile.mkstemp(prefix='.m4-quota-refresh-', dir=ATTESTATION.parent)
    try:
        os.fchmod(fd, 0o644)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(payload, f, separators=(',', ':'))
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        # Recheck live state immediately before publishing, and fail if the old file changed.
        referenced = verify()
        current = ATTESTATION.lstat()
        if (current.st_dev, current.st_ino, current.st_mtime_ns, current.st_size) != (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size):
            raise ValueError('attestation changed during verification')
        os.replace(name, ATTESTATION)
        name = None
        print(f'Refreshed {ATTESTATION}; referenced={referenced}, headroom={CAP-referenced}; no benchmark run')
    finally:
        if name is not None:
            os.unlink(name)


if __name__ == '__main__':
    refresh()
