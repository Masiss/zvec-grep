import os
import pathlib
import tempfile
import unittest

import sealed_unit_prototype as prototype


class SealedPrototypeTests(unittest.TestCase):
    def test_verified_copy_survives_path_replacement_but_rejects_wrong_digest(self):
        with tempfile.TemporaryDirectory(prefix='m4-sealed-test-', dir=prototype.SCRATCH) as directory:
            path = pathlib.Path(directory) / 'artifact'
            replacement = pathlib.Path(directory) / 'replacement'
            path.write_bytes(b'original')
            fd, digest = prototype.sealed_copy(path)
            try:
                replacement.write_bytes(b'different')
                os.replace(replacement, path)
                prototype.verify_artifact(fd, digest)
                self.assertEqual(os.pread(fd, 8, 0), b'original')
                with self.assertRaisesRegex(ValueError, 'digest mismatch'):
                    prototype.verify_artifact(fd, '0' * 64)
            finally:
                os.close(fd)

    def test_unsealed_copy_is_rejected_even_with_correct_digest(self):
        fd = os.memfd_create('unsealed-test', os.MFD_ALLOW_SEALING | os.MFD_CLOEXEC)
        try:
            os.write(fd, b'original')
            with self.assertRaisesRegex(ValueError, 'unsealed'):
                prototype.verify_artifact(fd, prototype.digest_fd(fd))
        finally:
            os.close(fd)


if __name__ == '__main__':
    unittest.main()
