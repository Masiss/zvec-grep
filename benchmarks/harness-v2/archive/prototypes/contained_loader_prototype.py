#!/usr/bin/env python3
"""Synthetic contained-memfd and $ORIGIN experiment; not a lifecycle launcher."""
from __future__ import annotations

import fcntl
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import sealed_unit_prototype as sealed

def _repo_root() -> pathlib.Path:
    for parent in pathlib.Path(__file__).resolve().parents:
        if (parent / 'AGENTS.md').is_file() and (parent / '.git').exists():
            return parent
    raise RuntimeError('repository root not found')

REPO = _repo_root()
SCRATCH = REPO / '.tmp'
PAD_BYTES = 1024 * 1024
CHILD_NAME = 'libm4_sealed_child_fixture.so'
LIB_NAME = 'libm4_sealed_parent_fixture.so'
CHILD_C = 'int m4_child_marker(void) { return 0x4d34; }\n'
LIB_C = 'extern int m4_child_marker(void); int m4_parent_marker(void) { return m4_child_marker(); }\n'
DRIVER_C = r'''
#define _GNU_SOURCE
#include <dlfcn.h>
#include <fcntl.h>
#include <link.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
extern int m4_parent_marker(void);
static int same(int fd, const char *path) {
    struct stat a, b;
    return fstat(fd, &a) == 0 && stat(path, &b) == 0 && a.st_dev == b.st_dev && a.st_ino == b.st_ino;
}
static int loaded_matches(int fd, const char *name) {
    void *handle = dlopen(name, RTLD_NOW | RTLD_NOLOAD);
    struct link_map *map = NULL;
    if (!handle || dlinfo(handle, RTLD_DI_LINKMAP, &map) || !map || !same(fd, map->l_name)) return 0;
    return dlclose(handle) == 0;
}
int main(int argc, char **argv) {
    if (argc == 2 && !strcmp(argv[1], "control")) return m4_parent_marker() == 0x4d34 ? 0 : 11;
    if (argc != 4) return 12;
    int executable = atoi(argv[1]), library = atoi(argv[2]), child = atoi(argv[3]);
    int required = F_SEAL_WRITE | F_SEAL_GROW | F_SEAL_SHRINK | F_SEAL_SEAL;
    for (int i = 1; i < argc; i++) {
        int seals = fcntl(atoi(argv[i]), F_GET_SEALS);
        if (seals < 0 || (seals & required) != required) return 13;
    }
    if (!same(executable, "/proc/self/exe")) return 14;
    if (!loaded_matches(library, "libm4_sealed_parent_fixture.so") ||
        !loaded_matches(child, "libm4_sealed_child_fixture.so")) return 15;
    if (m4_parent_marker() != 0x4d34) return 16;
    puts("{\"event\":\"sealed_dependency_graph\",\"result\":\"PASS\"}");
    return 0;
}
'''


def cgroup_stats() -> tuple[str, dict[str, str], dict[str, int]]:
    line = pathlib.Path('/proc/self/cgroup').read_text().strip()
    if not line.startswith('0::'):
        raise ValueError('expected unified cgroup')
    root = pathlib.Path('/sys/fs/cgroup') / line[3:].lstrip('/')
    names = ('memory.max', 'memory.swap.max', 'cpu.max', 'pids.max')
    limits = {name: (root / name).read_text().strip() for name in names}
    expected = {'memory.max': '536870912', 'memory.swap.max': '0',
                'cpu.max': '200000 100000', 'pids.max': '128'}
    if limits != expected:
        raise ValueError(f'cgroup policy mismatch: {limits}')
    stats = {key: int(value) for key, value in
             (row.split() for row in (root / 'memory.stat').read_text().splitlines())}
    return line, limits, {'memory_current': int((root / 'memory.current').read_text()),
                          'shmem': stats['shmem']}


def run_elf(binary: str, args: list[str], env: dict[str, str], fds: tuple[int, ...] = ()) -> subprocess.CompletedProcess[str]:
    return subprocess.run([binary, *args], env=env, pass_fds=fds, capture_output=True, text=True, timeout=10)


def inside(root: pathlib.Path) -> dict:
    cgroup, limits, before = cgroup_stats()
    binary, library, dependency = root / 'driver', root / LIB_NAME, root / CHILD_NAME
    base_env = {'PATH': '/usr/bin:/bin', 'LANG': 'C'}
    control = run_elf(str(binary), ['control'], {**base_env, 'LD_LIBRARY_PATH': str(root)})
    if control.returncode != 0:
        raise RuntimeError(f'original $ORIGIN fixture control failed: {control.stderr}')
    fds = []
    try:
        hashes = {}
        for name, path in [('binary', binary), ('library', library), ('dependency', dependency)]:
            fd, digest = sealed.sealed_copy(path)
            fds.append(fd)
            sealed.verify_artifact(fd, digest)
            hashes[name] = digest
        _, _, after = cgroup_stats()
        # Destroy the source path identity without touching the sealed descriptor copies.
        for path in (binary, library, dependency):
            replacement = path.with_name(path.name + '.replacement')
            replacement.write_bytes(b'not-an-elf')
            os.replace(replacement, path)
        executable, libfd, childfd = fds
        fd_args = [str(fd) for fd in fds]
        executable_path = f'/proc/self/fd/{executable}'
        missing = run_elf(executable_path, fd_args,
                          {**base_env, 'LD_PRELOAD': f'/proc/self/fd/{libfd}'}, tuple(fds))
        if missing.returncode == 0 or CHILD_NAME not in missing.stderr:
            raise RuntimeError(f'expected $ORIGIN dependency failure not seen: {missing.returncode}: {missing.stderr}')
        aliases = root / 'fd-aliases'
        aliases.mkdir(mode=0o700)
        (aliases / LIB_NAME).symlink_to(f'/proc/self/fd/{libfd}')
        (aliases / CHILD_NAME).symlink_to(f'/proc/self/fd/{childfd}')
        matched = run_elf(executable_path, fd_args,
                          {**base_env, 'LD_LIBRARY_PATH': str(aliases)}, tuple(fds))
        if matched.returncode or json.loads(matched.stdout).get('result') != 'PASS':
            raise RuntimeError(f'descriptor alias fixture failed: {matched.returncode}: {matched.stderr}: {matched.stdout}')
        return {'status': 'PASS_SYNTHETIC_ONLY', 'cgroup': cgroup, 'limits': limits,
                'memfd_creation_cgroup': cgroup, 'memory_before': before, 'memory_after': after,
                'artifact_sizes': [os.fstat(fd).st_size for fd in fds], 'hashes': hashes,
                'path_control_returncode': control.returncode,
                'descriptor_preload_without_dependency_path_returncode': missing.returncode,
                'descriptor_preload_without_dependency_path_stderr': missing.stderr.strip(),
                'private_symlink_alias_returncode': matched.returncode,
                'private_symlink_alias_stdout': json.loads(matched.stdout),
                'alias_safety': 'functional loader test only; writable same-UID directory is not an immutable namespace'}
    finally:
        for fd in fds:
            os.close(fd)


def run() -> dict:
    unit = f'm4-loader-prototype-{uuid.uuid4().hex[:12]}.service'
    with tempfile.TemporaryDirectory(prefix='m4-loader-probe-', dir=SCRATCH) as temp:
        root = pathlib.Path(temp)
        for name, content in [('child.c', CHILD_C), ('library.c', LIB_C), ('driver.c', DRIVER_C)]:
            (root / name).write_text(content)
        commands = [
            ['cc', '-O0', '-shared', '-fPIC', f'-Wl,-soname,{CHILD_NAME}', '-o', str(root / CHILD_NAME), str(root / 'child.c')],
            ['cc', '-O0', '-shared', '-fPIC', f'-Wl,-soname,{LIB_NAME}', '-Wl,-rpath,$ORIGIN',
             '-o', str(root / LIB_NAME), str(root / 'library.c'), '-L', str(root), '-l:' + CHILD_NAME],
            ['cc', '-O0', '-o', str(root / 'driver'), str(root / 'driver.c'), '-L', str(root),
             '-l:' + LIB_NAME, '-Wl,-rpath-link,' + str(root), '-ldl'],
        ]
        for command in commands:
            subprocess.run(command, check=True, timeout=30, capture_output=True,
                           env={**os.environ, 'TMPDIR': str(SCRATCH), 'TMP': str(SCRATCH), 'TEMP': str(SCRATCH)})
        # Padding is data, not a performance workload; it makes the memory charge observable.
        for path in (root / 'driver', root / LIB_NAME, root / CHILD_NAME):
            with path.open('ab') as out:
                out.write(bytes(PAD_BYTES))
        command = ['systemd-run', '--user', '--quiet', '--wait', '--pipe', '--collect', '--unit', unit,
                   f'--working-directory={REPO}', '--property=MemoryMax=512M', '--property=MemorySwapMax=0',
                   '--property=CPUQuota=200%', '--property=TasksMax=128', '--property=RuntimeMaxSec=30',
                   sys.executable, '-I', str(pathlib.Path(__file__).resolve()), '--inside', str(root)]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=REPO)
        properties = {}
        try:
            stdout, stderr = process.communicate(timeout=40)
            if process.returncode:
                raise RuntimeError(f'unit failed: {process.returncode}, stdout={stdout}, stderr={stderr}')
            result = json.loads(stdout)
        finally:
            if process.poll() is None:
                subprocess.run(['systemctl', '--user', 'stop', unit], timeout=10, capture_output=True)
                try:
                    process.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()
            state = subprocess.run(['systemctl', '--user', 'show', unit, '--property=ActiveState',
                                    '--property=MainPID', '--property=LoadState'],
                                   check=True, timeout=10, capture_output=True, text=True)
            properties = dict(line.split('=', 1) for line in state.stdout.splitlines() if '=' in line)
            if properties.get('ActiveState') not in ('inactive', 'failed') or properties.get('MainPID') != '0':
                raise RuntimeError(f'unit cleanup not verified: {properties}')
        result['unit'] = unit
        result['unit_after'] = properties
        result['stderr'] = stderr
        return result


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--inside':
        print(json.dumps(inside(pathlib.Path(sys.argv[2]).resolve()), sort_keys=True), flush=True)
    elif len(sys.argv) == 1:
        print(json.dumps(run(), indent=2, sort_keys=True))
    else:
        raise SystemExit('unexpected arguments')
