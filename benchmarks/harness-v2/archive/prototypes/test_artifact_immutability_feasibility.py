"""Small synthetic kernel probes; these do not change lifecycle identity guards."""
import errno
import fcntl
import os
import pathlib
import struct
import tempfile
import unittest


def _repo_root() -> pathlib.Path:
    for parent in pathlib.Path(__file__).resolve().parents:
        if (parent / 'AGENTS.md').is_file() and (parent / '.git').exists():
            return parent
    raise RuntimeError('repository root not found')

SCRATCH = _repo_root() / '.tmp'


class ArtifactImmutabilityFeasibilityTests(unittest.TestCase):
    def test_sealed_memfd_blocks_in_place_writes_and_truncation(self):
        if not hasattr(os, 'memfd_create') or not hasattr(fcntl, 'F_ADD_SEALS'):
            self.skipTest('memfd seals unavailable')
        fd = os.memfd_create('m4-identity-test', os.MFD_ALLOW_SEALING | os.MFD_CLOEXEC)
        self.addCleanup(os.close, fd)
        os.write(fd, b'original')
        seals = fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL
        fcntl.fcntl(fd, fcntl.F_ADD_SEALS, seals)
        self.assertEqual(fcntl.fcntl(fd, fcntl.F_GET_SEALS) & seals, seals)
        for operation in (lambda: os.pwrite(fd, b'X', 0), lambda: os.ftruncate(fd, 0)):
            with self.assertRaises(OSError) as raised:
                operation()
            self.assertEqual(raised.exception.errno, errno.EPERM)
        self.assertEqual(os.pread(fd, 8, 0), b'original')

    def test_open_file_descriptor_does_not_pin_path(self):
        with tempfile.TemporaryDirectory(prefix='m4-path-probe-', dir=SCRATCH) as directory:
            path = pathlib.Path(directory) / 'artifact'
            replacement = pathlib.Path(directory) / 'replacement'
            path.write_bytes(b'original')
            replacement.write_bytes(b'changed!')
            fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
            try:
                os.replace(replacement, path)
                self.assertEqual(path.read_bytes(), b'changed!')
                self.assertEqual(os.pread(fd, 8, 0), b'original')
            finally:
                os.close(fd)

    def test_verity_immutability_does_not_pin_path(self):
        # Linux UAPI: _IOW('f', 133, struct fsverity_enable_arg[128]).
        fs_ioc_enable_verity = 0x40806685
        fs_ioc_measure_verity = 0xC0046686
        with tempfile.TemporaryDirectory(prefix='m4-verity-probe-', dir=SCRATCH) as directory:
            path = pathlib.Path(directory) / 'artifact'
            replacement = pathlib.Path(directory) / 'replacement'
            original = b'original' * 1024
            changed = b'changed!' * 1024
            path.write_bytes(original)
            replacement.write_bytes(changed)
            fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
            try:
                args = struct.pack('=IIIIQIIQ11Q', 1, 1, 0, 0, 0, 0, 0, 0, *([0] * 11))
                try:
                    fcntl.ioctl(fd, fs_ioc_enable_verity, args)
                except OSError as exc:
                    if exc.errno in (errno.ENOTTY, errno.EOPNOTSUPP, errno.ENOSYS, errno.EPERM, errno.EACCES, errno.EINVAL):
                        self.skipTest(f'fs-verity probe did not enable (cause not established): {exc}')
                    raise
                digest = bytearray(struct.pack('=HH', 0, 32) + bytes(32))
                fcntl.ioctl(fd, fs_ioc_measure_verity, digest, True)
                algorithm, length = struct.unpack_from('=HH', digest)
                self.assertEqual((algorithm, length), (1, 32))
                with self.assertRaises(OSError) as raised:
                    os.open(path, os.O_WRONLY)
                self.assertEqual(raised.exception.errno, errno.EPERM)
                os.replace(replacement, path)
                self.assertEqual(path.read_bytes(), changed)
                self.assertEqual(os.pread(fd, len(original), 0), original)
                with os.fdopen(os.open(path, os.O_RDONLY), 'rb') as new_file:
                    with self.assertRaises(OSError) as raised:
                        fcntl.ioctl(new_file.fileno(), fs_ioc_measure_verity,
                                    bytearray(struct.pack('=HH', 0, 32) + bytes(32)), True)
                    self.assertEqual(raised.exception.errno, errno.ENODATA)
            finally:
                os.close(fd)


if __name__ == '__main__':
    unittest.main()
