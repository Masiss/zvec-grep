#!/usr/bin/env python3
"""Focused syscall/fault recovery checks; no performance measurement.

Requires a closed --vector-delta-candidate fixture (generation/highwater 11).
strace observes and injects only the main thread's fsync calls. Every injected
call is verified against its decoded fd path; a mismatch is a test failure.
"""
import argparse
import hashlib
import json
import os
import pathlib
import shutil
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=pathlib.Path, required=True)
    parser.add_argument('--old-binary', type=pathlib.Path, required=True)
    parser.add_argument('--fixture', type=pathlib.Path, required=True)
    parser.add_argument('--dictionary', type=pathlib.Path, required=True)
    parser.add_argument('--workspace', type=pathlib.Path, required=True)
    parser.add_argument('--output', type=pathlib.Path, required=True)
    args = parser.parse_args()
    args.workspace.mkdir(parents=True, exist_ok=False)
    args.output.mkdir(parents=True, exist_ok=False)
    receipts = []
    suffix = '/generation-12/fts/wal.log'

    def run(label, binary, stage, inject=None):
        root = args.workspace / label
        shutil.copytree(args.fixture, root)
        trace = args.output / (label + '.trace')
        cmd = ['strace', '-qq', '-yy', '-e', 'trace=fsync,ftruncate', '-o', str(trace)]
        if inject:
            cmd += ['-e', inject]
        cmd += [str(binary), '--delta-crash-child', str(root), str(args.dictionary), stage]
        env = os.environ.copy()
        for key in list(env):
            if key.startswith('M4_') or key == 'LD_PRELOAD':
                env.pop(key)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=90, env=env)
        (args.output / (label + '.stderr')).write_text(result.stderr)
        lines = trace.read_text().splitlines()
        fsyncs = [line for line in lines if line.startswith('fsync(')]
        truncates = [i for i, line in enumerate(lines)
                     if line.startswith('ftruncate(') and suffix in line and ', 0)' in line]
        assert truncates, (label, 'no WAL truncate observed')
        outer = [line for i, line in enumerate(lines)
                 if i > truncates[-1] and line.startswith('fsync(') and suffix in line]
        receipt = dict(label=label, argv=cmd, returncode=result.returncode,
                       outer_wal_fsync_after_truncate=outer)
        receipts.append(receipt)
        (args.output / 'commands.json').write_text(json.dumps(receipts, indent=2)+'\n')
        return root, result.returncode, fsyncs, outer

    def recover(label, root, generation):
        command = [str(args.binary), '--delta-recover-check', str(root),
                   str(args.dictionary), str(generation), '12']
        result = subprocess.run(command, capture_output=True, text=True, timeout=90)
        (args.output / (label + '.recovery.log')).write_text(result.stdout + result.stderr)
        assert result.returncode == 0, (label, result.returncode, result.stderr)

    _, rc, _, old_outer = run('old-elision', args.old_binary, 'after_stage')
    assert rc == 86 and not old_outer, 'old binary must demonstrate the missing outer WAL fsync'
    root, rc, fsyncs, outer = run('restored-stage', args.binary, 'after_stage')
    assert rc == 86 and len(outer) == 1 and '= 0' in outer[0]
    recover('restored-stage', root, 11)
    nth = fsyncs.index(outer[0]) + 1
    root, rc, _, outer = run('wal-sync-eio', args.binary, 'after_stage',
                             f'inject=fsync:error=EIO:when={nth}')
    assert rc == 91 and len(outer) == 1 and 'EIO' in outer[0] and 'INJECTED' in outer[0]
    recover('wal-sync-eio', root, 11)
    root, rc, _, outer = run('wal-sync-sigkill', args.binary, 'after_stage',
                             f'inject=fsync:signal=SIGKILL:when={nth}')
    # Signal injection terminates the syscall with '= ?'; strace does not
    # append the errno-injection marker to that line on all versions.
    assert rc in (-9, 137) and len(outer) == 1
    assert 'killed by SIGKILL' in (args.output / 'wal-sync-sigkill.trace').read_text()
    recover('wal-sync-sigkill', root, 11)
    root, rc, _, outer = run('published-wal', args.binary, 'after_publish_wal')
    assert rc == 87 and len(outer) == 1 and '= 0' in outer[0]
    recover('published-wal', root, 12)
    (args.output / 'summary.json').write_text(json.dumps(dict(
        status='PASS', binary_sha256=hashlib.sha256(args.binary.read_bytes()).hexdigest(),
        old_binary_sha256=hashlib.sha256(args.old_binary.read_bytes()).hexdigest(),
        cases=5, recovery_checks=4, injected_fsync_ordinal=nth,
        scope='Process-crash/EIO/SIGKILL verification and observed syscall ordering, NOT physical power-loss simulation; no benchmark.'
    ), indent=2)+'\n')
    print('outer sync contract: 5 cases / 4 recovery checks PASS')


if __name__ == '__main__':
    main()
