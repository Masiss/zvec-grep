import pathlib
import tempfile
import unittest
from unittest import mock

import refresh_workspace_quota_attestation as refresh

FS = f'btrfs {refresh.UUID} /\n'
STATUS = 'Enabled: yes\nMode: squota (simplified accounting)\nInconsistent: no\nOverride limits: no\n'
GROUP = '0/259 8384512 8384512 167772160 path\n'


class RefreshTests(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(refresh.validate(FS, '259\n', STATUS, GROUP), 8384512)

    def test_rejects_bad_evidence(self):
        cases = [
            ('btrfs bad /', '259', STATUS, GROUP),
            (FS, '258', STATUS, GROUP),
            (FS, '259', STATUS.replace('Inconsistent: no', 'Inconsistent: yes'), GROUP),
            (FS, '259', STATUS.replace('Override limits: no', 'Override limits: yes'), GROUP),
            (FS, '259', STATUS, GROUP.replace('167772160', '167772161')),
            (FS, '259', STATUS, '0/259 100663297 100663297 167772160 path'),
            (FS, '259', STATUS, GROUP + GROUP),
            (FS, '259', STATUS, '0/259 bad 0 134217728 path'),
        ]
        for args in cases:
            with self.subTest(args=args), self.assertRaises(ValueError):
                refresh.validate(*args)

    def test_refuses_unprivileged_before_writing(self):
        with mock.patch.object(refresh.os, 'geteuid', return_value=1234), self.assertRaises(PermissionError):
            refresh.refresh()

    def test_invalid_live_verification_preserves_attestation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / 'attestation'
            path.write_text('original')
            path.chmod(0o644)
            real_lstat = path.lstat()
            fake_info = mock.Mock(st_mode=real_lstat.st_mode, st_uid=0, st_gid=0)
            with mock.patch.object(refresh, 'ATTESTATION', path), \
                 mock.patch.object(refresh.os, 'geteuid', return_value=0), \
                 mock.patch.object(pathlib.Path, 'lstat', return_value=fake_info), \
                 mock.patch.object(refresh, 'verify', side_effect=ValueError('bad live quota')), \
                 self.assertRaises(ValueError):
                refresh.refresh()
            self.assertEqual(path.read_text(), 'original')

    def test_atomic_publish_and_second_check_failure(self):
        for second_fails in (False, True):
            with self.subTest(second_fails=second_fails), tempfile.TemporaryDirectory() as directory:
                path = pathlib.Path(directory) / 'attestation'
                path.write_text('original')
                path.chmod(0o644)
                info = path.lstat()
                fake = mock.Mock(st_mode=info.st_mode, st_uid=0, st_gid=0,
                                 st_dev=info.st_dev, st_ino=info.st_ino,
                                 st_mtime_ns=info.st_mtime_ns, st_size=info.st_size)
                verification = [8384512, ValueError('changed') if second_fails else 8384512]
                with mock.patch.object(refresh, 'ATTESTATION', path), \
                     mock.patch.object(refresh.os, 'geteuid', return_value=0), \
                     mock.patch.object(pathlib.Path, 'lstat', return_value=fake), \
                     mock.patch.object(refresh, 'verify', side_effect=verification):
                    if second_fails:
                        with self.assertRaises(ValueError):
                            refresh.refresh()
                        self.assertEqual(path.read_text(), 'original')
                    else:
                        refresh.refresh()
                        self.assertIn('"qgroup_id":"0/259"', path.read_text())
                self.assertEqual(list(path.parent.iterdir()), [path])


if __name__ == '__main__':
    unittest.main()
