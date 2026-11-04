#!/usr/bin/env python3
"""One tiny systemd-unit experiment, not an M4 launcher or identity guard."""
from __future__ import annotations

import array
import errno
import fcntl
import hashlib
import json
import os
import pathlib
import socket
import struct
import subprocess
import sys
import tempfile
import uuid

def _repo_root() -> pathlib.Path:
    for parent in pathlib.Path(__file__).resolve().parents:
        if (parent / 'AGENTS.md').is_file() and (parent / '.git').exists():
            return parent
    raise RuntimeError('repository root not found')

REPO = _repo_root()
SCRATCH = REPO / '.tmp'
SEALS = fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL
C_BINARY = r'''
#define _GNU_SOURCE
#include <dlfcn.h>
#include <fcntl.h>
#include <link.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/stat.h>
#include <unistd.h>
extern int sealed_fixture_marker(void);
static int same(int fd, const char *path) {
    struct stat a, b;
    return fstat(fd, &a) == 0 && stat(path, &b) == 0 && a.st_dev == b.st_dev && a.st_ino == b.st_ino;
}
int main(int argc, char **argv) {
    if (argc != 3) return 11;
    int executable = atoi(argv[1]), library = atoi(argv[2]);
    int required = F_SEAL_WRITE | F_SEAL_GROW | F_SEAL_SHRINK | F_SEAL_SEAL;
    int a = fcntl(executable, F_GET_SEALS), b = fcntl(library, F_GET_SEALS);
    if (a < 0 || b < 0 || (a & required) != required || (b & required) != required) return 12;
    if (!same(executable, "/proc/self/exe")) return 13;
    void *handle = dlopen("libfixture.so", RTLD_NOW | RTLD_NOLOAD);
    if (!handle) { fprintf(stderr, "needed library not loaded: %s\n", dlerror()); return 14; }
    struct link_map *map = NULL;
    if (dlinfo(handle, RTLD_DI_LINKMAP, &map) || !map || !same(library, map->l_name)) return 15;
    int (*marker)(void) = (int (*)(void))dlsym(handle, "sealed_fixture_marker");
    if (!marker || marker() != 0x4d34 || sealed_fixture_marker() != 0x4d34) return 16;
    puts("{\"event\":\"sealed_exec_and_needed_library\",\"result\":\"PASS\"}");
    return dlclose(handle) ? 17 : 0;
}
'''
C_LIBRARY = 'int sealed_fixture_marker(void) { return 0x4d34; }\n'


def digest_fd(fd: int) -> str:
    digest = hashlib.sha256()
    offset = 0
    while True:
        data = os.pread(fd, 65536, offset)
        if not data:
            return digest.hexdigest()
        digest.update(data)
        offset += len(data)


def sealed_copy(path: pathlib.Path) -> tuple[int, str]:
    fd = os.memfd_create('m4-synthetic-artifact', os.MFD_ALLOW_SEALING | os.MFD_CLOEXEC)
    try:
        with path.open('rb') as source:
            while chunk := source.read(65536):
                view = memoryview(chunk)
                while view:
                    view = view[os.write(fd, view):]
        expected = digest_fd(fd)
        fcntl.fcntl(fd, fcntl.F_ADD_SEALS, SEALS)
        if fcntl.fcntl(fd, fcntl.F_GET_SEALS) & SEALS != SEALS:
            raise RuntimeError('missing required seals')
        try:
            os.pwrite(fd, b'x', 0)
        except OSError as exc:
            if exc.errno != errno.EPERM:
                raise
        else:
            raise RuntimeError('sealed write unexpectedly succeeded')
        # execve refuses a writable file description even when its inode is sealed.
        readonly = os.open(f'/proc/self/fd/{fd}', os.O_RDONLY | os.O_CLOEXEC)
        return readonly, expected
    finally:
        os.close(fd)


def check_seals(fd: int) -> None:
    if fcntl.fcntl(fd, fcntl.F_GET_SEALS) & SEALS != SEALS:
        raise ValueError('unsealed artifact rejected')


def verify_artifact(fd: int, expected: str) -> None:
    check_seals(fd)
    if digest_fd(fd) != expected:
        raise ValueError('artifact digest mismatch')


def child(socket_path: str) -> None:
    with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as client:
        client.settimeout(10)
        client.connect(socket_path)
        payload, ancillary, flags, _ = client.recvmsg(4096, socket.CMSG_SPACE(3 * array.array('i').itemsize))
        if flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC):
            raise RuntimeError('truncated descriptor transfer')
        fds = array.array('i')
        for level, kind, data in ancillary:
            if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                fds.frombytes(data[:len(data) - len(data) % fds.itemsize])
        if len(fds) != 3:
            raise RuntimeError('exactly three descriptors required')
        binary, library, unsealed = fds
        try:
            check_seals(unsealed)
        except ValueError:
            pass
        else:
            raise RuntimeError('unsealed descriptor was accepted')
        os.close(unsealed)
        expected = json.loads(payload)
        for fd, name in ((binary, 'binary'), (library, 'library')):
            verify_artifact(fd, expected[name])
            os.set_inheritable(fd, True)
        cgroup_line = pathlib.Path('/proc/self/cgroup').read_text().strip()
        if not cgroup_line.startswith('0::'):
            raise RuntimeError('expected unified cgroup')
        cg = pathlib.Path('/sys/fs/cgroup') / cgroup_line[3:].lstrip('/')
        expected_limits = {'memory.max': '536870912', 'memory.swap.max': '0',
                           'pids.max': '128', 'cpu.max': '200000 100000'}
        limits = {name: (cg / name).read_text().strip() for name in expected_limits}
        if limits != expected_limits:
            raise RuntimeError(f'unit limits differ: {limits}')
        print(json.dumps({'event': 'verified_in_unit', 'seals': SEALS, 'hashes': expected,
                          'unsealed_rejected': True, 'cgroup': cgroup_line, 'limits': limits}), flush=True)
        os.execve(f'/proc/self/fd/{binary}', [f'/proc/self/fd/{binary}', str(binary), str(library)],
                  {'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LD_PRELOAD': f'/proc/self/fd/{library}'})


def run() -> dict:
    unit = f'm4-sealed-prototype-{uuid.uuid4().hex[:12]}.service'
    process = None
    fds: list[int] = []
    with tempfile.TemporaryDirectory(prefix='m4-sealed-unit-', dir=SCRATCH) as temp:
        root = pathlib.Path(temp)
        binary_source, library_source = root / 'fixture.c', root / 'library.c'
        binary, library, sock_path = root / 'fixture', root / 'libfixture.so', root / 'transfer.sock'
        binary_source.write_text(C_BINARY)
        library_source.write_text(C_LIBRARY)
        for command in (['cc', '-O0', '-shared', '-fPIC', '-Wl,-soname,libfixture.so',
                         '-o', str(library), str(library_source)],
                        ['cc', '-O0', '-o', str(binary), str(binary_source), '-L', str(root), '-lfixture', '-ldl']):
            subprocess.run(command, cwd=REPO, check=True, timeout=30, capture_output=True,
                           env={**os.environ, 'TMPDIR': str(SCRATCH), 'TMP': str(SCRATCH), 'TEMP': str(SCRATCH)})
        binary_fd, binary_hash = sealed_copy(binary)
        fds.append(binary_fd)
        library_fd, library_hash = sealed_copy(library)
        fds.append(library_fd)
        unsealed = os.memfd_create('m4-unsealed-negative', os.MFD_ALLOW_SEALING | os.MFD_CLOEXEC)
        fds.append(unsealed)
        os.write(unsealed, b'not sealed')
        # Replace both compiler output paths after pinning the sealed copies.
        for path in (binary, library):
            replacement = path.with_suffix('.replacement')
            replacement.write_bytes(b'not-an-elf')
            os.replace(replacement, path)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        try:
            server.bind(str(sock_path))
            os.chmod(sock_path, 0o600)
            server.listen(1)
            server.settimeout(15)
            command = ['systemd-run', '--user', '--quiet', '--wait', '--pipe', '--collect',
                       '--unit', unit, f'--working-directory={REPO}',
                       '--property=MemoryMax=512M', '--property=MemorySwapMax=0',
                       '--property=CPUQuota=200%', '--property=TasksMax=128',
                       '--property=RuntimeMaxSec=30', sys.executable, '-I', str(pathlib.Path(__file__).resolve()),
                       '--child', str(sock_path)]
            process = subprocess.Popen(command, cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            connection, _ = server.accept()
            with connection:
                pid, uid, _ = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                state = subprocess.run(['systemctl', '--user', 'show', unit, '--property=MainPID', '--value'],
                                       check=True, timeout=10, capture_output=True, text=True)
                if uid != os.getuid() or pid != int(state.stdout.strip()):
                    raise RuntimeError('socket peer is not the launched unit main process')
                rights = array.array('i', fds)
                connection.sendmsg([json.dumps({'binary': binary_hash, 'library': library_hash}).encode()],
                                   [(socket.SOL_SOCKET, socket.SCM_RIGHTS, rights)])
            stdout, stderr = process.communicate(timeout=35)
            if process.returncode:
                raise RuntimeError(f'systemd unit failed: rc={process.returncode}; stdout={stdout}; stderr={stderr}')
            state = subprocess.run(['systemctl', '--user', 'show', unit, '--property=ActiveState',
                                    '--property=MainPID', '--property=LoadState'],
                                   check=True, timeout=10, capture_output=True, text=True)
            properties = dict(line.split('=', 1) for line in state.stdout.splitlines() if '=' in line)
            if properties.get('ActiveState') not in ('inactive', 'failed') or properties.get('MainPID') != '0':
                raise RuntimeError(f'unit cleanup not verified: {properties}')
            events = [json.loads(line) for line in stdout.splitlines() if line.startswith('{')]
            if ([event.get('event') for event in events] != ['verified_in_unit', 'sealed_exec_and_needed_library'] or
                    events[-1].get('result') != 'PASS'):
                raise RuntimeError(f'missing expected child evidence: {stdout}; stderr={stderr}')
            return {'status': 'PASS_SYNTHETIC_ONLY', 'unit': unit, 'unit_after': properties, 'events': events,
                    'binary_bytes': os.fstat(binary_fd).st_size, 'library_bytes': os.fstat(library_fd).st_size,
                    'stderr': stderr}
        finally:
            server.close()
            for fd in fds:
                os.close(fd)
            if process is not None and process.poll() is None:
                subprocess.run(['systemctl', '--user', 'stop', unit], timeout=10, capture_output=True)
                try:
                    process.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--child':
        child(sys.argv[2])
    elif len(sys.argv) == 1:
        print(json.dumps(run(), indent=2))
    else:
        raise SystemExit('unexpected arguments')
