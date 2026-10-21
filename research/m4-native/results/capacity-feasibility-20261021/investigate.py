#!/usr/bin/env python3
"""Bounded read-only host probes + <=128KiB synthetic file/model checks. No M4 execution.
Run from repository root; artifact output is adjacent. No quota mutation/sync/rescan.
"""
import ctypes
import datetime
import errno
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
WORK = ROOT / 'research/m4-native/.work/tmp'
SOURCES = [
    'research/m4-native/src/index.cc',
    'research/m4-native/src/format.cc',
    'research/m4-native/src/stream_merge.cc',
    'research/m4-native/tests/m4_bridge_integration.cc',
    'research/m4-native/tools/m4_containment_runner.py',
    'research/m4-native/docs/benchmark-policy.json',
    'research/m4-native/results/autonomous-campaign-20261020/resource-plan.approved-pair-3.json',
    'research/m4-native/results/autonomous-campaign-20261020/campaign-ledger.json',
    'research/m4-native/results/autonomous-campaign-20261020/retained-resource-evidence.json',
]


def read(path):
    try:
        return Path(path).read_text()
    except OSError as exc:
        return {'error': str(exc)}


def command(args):
    started = time.monotonic_ns()
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=5, check=False)
        result = {'argv': args, 'returncode': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr}
    except (OSError, subprocess.TimeoutExpired) as exc:
        result = {'argv': args, 'error': str(exc)}
    result['elapsed_ns'] = time.monotonic_ns() - started
    return result


def host():
    return {
        'utc': datetime.datetime.now(datetime.UTC).isoformat(),
        'loadavg': read('/proc/loadavg'), 'meminfo': read('/proc/meminfo'),
        'pressure': {k: read('/proc/pressure/' + k) for k in ('cpu', 'io', 'memory')},
        'affinity': sorted(os.sched_getaffinity(0)),
        'processes': command(['ps', '-eo', 'pid,ppid,comm,pcpu,rss,vsz,etimes', '--sort=-rss']),
        'statvfs_var_tmp': dict(zip(('bsize', 'frsize', 'blocks', 'bfree', 'bavail', 'files',
                                   'ffree', 'favail', 'flag', 'namemax'), os.statvfs('/var/tmp'), strict=True)),  # noqa: S108 -- read-only mount stat
    }


U64 = ctypes.c_uint64
U32 = ctypes.c_uint32


class Timespec(ctypes.Structure):
    _fields_ = [('sec', U64), ('nsec', U32)]


class Subvol(ctypes.Structure):
    _fields_ = [('treeid', U64), ('name', ctypes.c_char * 256),
                *[(k, U64) for k in ('parent_id', 'dirid', 'generation', 'flags')],
                *[(k, ctypes.c_ubyte * 16) for k in ('uuid', 'parent_uuid', 'received_uuid')],
                *[(k, U64) for k in ('ctransid', 'otransid', 'stransid', 'rtransid')],
                *[(k, Timespec) for k in ('ctime', 'otime', 'stime', 'rtime')],
                ('reserved', U64 * 8)]


class SearchKey(ctypes.Structure):
    _fields_ = [*[(k, U64) for k in ('tree_id', 'min_objectid', 'max_objectid', 'min_offset',
                                   'max_offset', 'min_transid', 'max_transid')],
                *[(k, U32) for k in ('min_type', 'max_type', 'nr_items', 'unused')],
                *[(k, U64) for k in ('unused1', 'unused2', 'unused3', 'unused4')]]


class Search(ctypes.Structure):
    _fields_ = [('key', SearchKey), ('buf', ctypes.c_ubyte * (4096 - ctypes.sizeof(SearchKey)))]


def ioctl_read(path, request, data):
    started = time.monotonic_ns()
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        buffer = bytearray(bytes(data))
        fcntl.ioctl(fd, request, buffer, True)
        ctypes.memmove(ctypes.addressof(data), bytes(buffer), len(buffer))
        return {'ok': True, 'elapsed_ns': time.monotonic_ns() - started}
    except OSError as exc:
        return {'ok': False, 'errno': exc.errno, 'errno_name': errno.errorcode.get(exc.errno) if exc.errno is not None else None,
                'error': str(exc), 'elapsed_ns': time.monotonic_ns() - started}
    finally:
        os.close(fd)


def btrfs():
    # Linux asm-generic ioctl ABI on this x86_64 host, matched to local linux/btrfs.h.
    assert os.uname().machine == 'x86_64'
    assert ctypes.sizeof(Subvol) == 504 and ctypes.sizeof(Search) == 4096
    result = {'uname': list(os.uname()), 'euid': os.geteuid(), 'btrfs_cli': shutil.which('btrfs')}
    result['mounts'] = [command(['findmnt', '-T', p, '-o', 'TARGET,SOURCE,FSTYPE,OPTIONS'])
                        for p in ('/var/tmp', str(WORK))]  # noqa: S108 -- read-only findmnt target
    result['subvolumes'] = {}
    for path in ('/var/tmp', str(WORK)):  # noqa: S108 -- O_RDONLY capability query
        data = Subvol()
        row = ioctl_read(path, (2 << 30) | (504 << 16) | (0x94 << 8) | 60, data)
        if row['ok']:
            row.update(treeid=data.treeid, parent_id=data.parent_id,
                       name=bytes(data.name).decode(errors='replace'), generation=data.generation)
        result['subvolumes'][path] = row
    data = Search()
    data.key.tree_id = 8  # QUOTA_TREE_OBJECTID
    data.key.min_type = data.key.max_type = 240  # QGROUP_STATUS_KEY
    data.key.max_transid = (1 << 64) - 1
    data.key.nr_items = 1  # only (objectid=0,type=240,offset=0); never whole-tree enumeration
    row = ioctl_read('/var/tmp', (3 << 30) | (4096 << 16) | (0x94 << 8) | 17, data)  # noqa: S108 -- read-only ioctl
    if row['ok']:
        row.update(nr_items=data.key.nr_items, buffer_hex=bytes(data.buf).hex())
    result['quota_status_item_read'] = row
    result['sysfs'] = {}
    for fs in sorted(Path('/sys/fs/btrfs').iterdir()):
        if fs.name == 'features':
            continue
        paths = [fs / k for k in ('nodesize', 'sectorsize', 'quota_override')]
        paths += list((fs / 'allocation').glob('*/*'))
        paths += list((fs / 'qgroups').glob('*'))
        result['sysfs'][fs.name] = {
            'qgroups_dir_exists': (fs / 'qgroups').exists(),
            'filesystem_features': sorted(p.name for p in (fs / 'features').iterdir()),
            'values': {str(p): read(p) for p in paths if p.is_file()},
        }
    result['kernel_simple_quota_feature'] = read('/sys/fs/btrfs/features/simple_quota')
    result['interpretation'] = 'Capability/scope only. EPERM or missing sysfs is not proof quotas disabled; no scoped quota configured/verified by this investigation.'
    return result


def tiny_checks():
    """Counter invariant is only for participating dense writers, not the native arm."""
    checks = []
    class Budget:
        def __init__(self, limit):
            self.limit, self.reserved = limit, 0
        def charge(self, n):
            if n < 0 or n > self.limit - self.reserved:
                return False
            self.reserved += n  # never refund: conservative cumulative admitted bytes
            return True
    b = Budget(65536)
    assert b.charge(32768) and b.charge(32768) and not b.charge(1)
    assert not b.charge(-1) and not b.charge(1 << 64)
    checks.append('exact boundary, negative, and oversized request rejected before write')
    partial = Budget(10)
    assert partial.charge(8) and not partial.charge(3)  # reserve all even after a partial/failed write
    checks.append('no refund after partial/failed write is conservative')
    WORK.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='capacity-feasibility-', dir=WORK) as tmp:
        root = Path(tmp)
        dense = root / 'dense'
        dense.write_bytes(b'x' * 65536)
        linked = root / 'linked'
        os.link(dense, linked)
        checks.append('hardlink preserves inode/size; path sum double-counts this inode')
        assert dense.stat().st_ino == linked.stat().st_ino
        sparse = root / 'sparse'
        with sparse.open('wb') as f:
            f.truncate(1 << 20)  # logical only; no 1MiB payload write
        assert sparse.stat().st_size == 1 << 20
        sparse_stat = {'size': sparse.stat().st_size, 'st_blocks_bytes': sparse.stat().st_blocks * 512}
        checks.append('truncate bypasses byte-write-only counter: sparse apparent=1MiB')
        with dense.open('rb') as f:
            dense.unlink()
            linked.unlink()
            unlinked = os.fstat(f.fileno())
            assert unlinked.st_nlink == 0 and unlinked.st_size == 65536
            checks.append('open-unlinked data has size but no directory entry; namespace walk is not lifetime storage')
        small = root / 'small'
        small.write_bytes(b'y')
        with small.open('rb') as f:
            os.fsync(f.fileno())  # this owned 1-byte file only; NOT filesystem-wide sync
        small_stat = {'size': small.stat().st_size, 'st_blocks_bytes': small.stat().st_blocks * 512}
        checks.append('single-byte file allocation granularity observed separately from apparent bytes')
    return {'checks_passed': checks, 'count': len(checks), 'sparse': sparse_stat, 'small': small_stat,
            'scope': 'feasibility counterexample/model only; no M4/runtime cap implementation or native certification',
            'scratch_removed': not root.exists(), 'payload_bytes_created': 65537}


def main():
    out = {'scope': 'bounded feasibility investigation; no benchmark, quota admin, guard change, or native M4 launch',
           'host_before': host(), 'btrfs': btrfs(), 'tiny_checks': tiny_checks(),
           'source_sha256': {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in SOURCES},
           'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), 'host_after': host()}
    target = HERE / 'probe.json'
    target.write_text(json.dumps(out, indent=2) + '\n')
    print(json.dumps({'receipt': str(target), 'tiny_checks': out['tiny_checks'], 'btrfs': {
        k: out['btrfs'][k] for k in ('btrfs_cli', 'subvolumes', 'quota_status_item_read')}}))


if __name__ == '__main__':
    main()
