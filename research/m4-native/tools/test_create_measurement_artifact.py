import os
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock

import create_measurement_artifact as artifact


class MeasurementArtifactTests(unittest.TestCase):
    def test_debug_only_removal_preserves_load_image_and_detects_code_mutation(self):
        root = artifact.TARGET.resolve() / '.m4-measurement'
        root.mkdir(mode=0o700, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='m4-artifact-test-', dir=root) as directory:
            path = pathlib.Path(directory)
            source_code, source, output = path / 'fixture.c', path / 'zg_engine-fixture', path / 'zg_engine-nodebug'
            source_code.write_text('#include <stdio.h>\nint main(void) { puts("fixture"); return 0; }\n')
            subprocess.run(['cc', '-g', '-O0', '-Wl,--discard-sframe', str(source_code), '-o', str(source)], check=True, timeout=30)
            result = artifact.create(source, output)
            self.assertTrue(result['load_image_and_program_headers_equal'])
            self.assertLess(result['output_bytes'], result['source_bytes'])
            self.assertEqual(artifact.load_segments(source), artifact.load_segments(output))
            self.assertEqual(subprocess.check_output([str(source)]), subprocess.check_output([str(output)]))
            with self.assertRaisesRegex(ValueError, 'new file'):
                artifact.create(source, output)
            first_offset, first_size, _ = artifact.load_segments(output)[1][0]
            mutation_offset = first_offset + first_size - 1
            fd = os.open(output, os.O_RDWR)
            try:
                old = os.pread(fd, 1, mutation_offset)
                os.pwrite(fd, bytes([old[0] ^ 1]), mutation_offset)
            finally:
                os.close(fd)
            self.assertNotEqual(artifact.load_segments(source), artifact.load_segments(output))

    def test_load_image_mismatch_fails_closed_and_removes_owned_output(self):
        root = artifact.TARGET.resolve() / '.m4-measurement'
        root.mkdir(mode=0o700, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='m4-artifact-fail-test-', dir=root) as directory:
            path = pathlib.Path(directory)
            source_code, source, output = path / 'fixture.c', path / 'zg_engine-fixture', path / 'zg_engine-nodebug'
            source_code.write_text('int main(void) { return 0; }\n')
            subprocess.run(['cc', '-g', '-O0', '-Wl,--discard-sframe', str(source_code), '-o', str(source)], check=True, timeout=30)
            before = artifact.containment.file_sha256(source)
            with mock.patch.object(artifact, 'load_segments', side_effect=[(b'original', []), (b'changed', [])]):
                with self.assertRaisesRegex(ValueError, 'load image'):
                    artifact.create(source, output)
            self.assertFalse(output.exists())
            self.assertEqual(artifact.containment.file_sha256(source), before)

    def test_rejects_source_outside_target_before_output(self):
        with tempfile.TemporaryDirectory() as directory:
            source = pathlib.Path(directory) / 'foreign'
            source.write_bytes(b'foreign')
            output = artifact.TARGET / '.m4-measurement' / 'zg_engine-forbidden-test'
            with self.assertRaisesRegex(ValueError, 'inside the Rust target'):
                artifact.create(source, output)
            self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
