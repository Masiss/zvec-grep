"""Opt-in ELF artifact creation with a preserved load image."""
from __future__ import annotations

import hashlib
import os
import pathlib
import re
import struct
import subprocess


def file_sha256(path: pathlib.Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def debug_sections(path: pathlib.Path) -> list[str]:
    result = subprocess.run(
        ['readelf', '-W', '-S', str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    sections = []
    pattern = re.compile(
        r'^\s*\[\s*\d+\]\s+(\S+)\s+\S+\s+[\da-fA-F]+\s+[\da-fA-F]+\s+'
        r'[\da-fA-F]+\s+[\da-fA-F]+\s*(.*?)\s+(\d+)\s+(\d+)\s+(\d+)\s*$'
    )
    for line in result.stdout.splitlines():
        match = pattern.match(line)
        if (
            match
            and match.group(1).startswith(('.debug_', '.zdebug_'))
            and 'A' not in match.group(2)
        ):
            sections.append(match.group(1))
    if not sections:
        raise ValueError('no removable non-allocatable debug sections found')
    return sections


def load_segments(path: pathlib.Path) -> tuple[bytes, list[tuple[int, int, bytes]]]:
    with path.open('rb') as source:
        header = source.read(64)
        if len(header) != 64 or header[:6] != b'\x7fELF\x02\x01':
            raise ValueError('requires little-endian ELF64')
        phoff = struct.unpack_from('<Q', header, 32)[0]
        phentsize, phnum = struct.unpack_from('<HH', header, 54)
        if phentsize != 56 or phnum == 0 or phnum > 1024:
            raise ValueError('invalid program-header table')
        source.seek(phoff)
        phdrs = source.read(phentsize * phnum)
        if len(phdrs) != phentsize * phnum:
            raise ValueError('truncated program-header table')
        segments = []
        for entry in struct.iter_unpack('<IIQQQQQQ', phdrs):
            typ, _, offset, _, _, filesz, _, _ = entry
            if typ != 1:
                continue
            source.seek(offset)
            digest = hashlib.sha256()
            position = offset
            remaining = filesz
            while remaining:
                data = bytearray(source.read(min(1024 * 1024, remaining)))
                if not data:
                    raise ValueError('truncated load segment')
                # objcopy may move/change the section-header table; those header
                # fields are not used to map the ELF program into memory.
                for start, end in ((40, 48), (60, 64)):
                    lo, hi = max(start, position), min(end, position + len(data))
                    if lo < hi:
                        data[lo - position:hi - position] = b'\0' * (hi - lo)
                digest.update(data)
                position += len(data)
                remaining -= len(data)
            segments.append((offset, filesz, digest.digest()))
        if not segments:
            raise ValueError('ELF has no load segments')
        normalized = bytearray(header)
        normalized[40:48] = bytes(8)
        normalized[60:64] = bytes(4)
        return bytes(normalized) + phdrs, segments


def create(source: pathlib.Path, output: pathlib.Path, target: pathlib.Path) -> dict:
    source = source.resolve(strict=True)
    target = target.resolve(strict=True)
    output = output.absolute()
    measurement_root = target / '.m4-measurement'
    if not source.is_relative_to(target) or not source.is_file():
        raise ValueError('source must be an existing test binary inside the Rust target')
    if not output.is_relative_to(measurement_root) or output.exists():
        raise ValueError('output must be a new file inside the opt-in measurement directory')
    sections = debug_sections(source)
    before_hash = file_sha256(source)
    before_segments = load_segments(source)
    measurement_root.mkdir(mode=0o700, exist_ok=True)
    if measurement_root.is_symlink() or measurement_root.resolve().parent != target:
        raise ValueError('measurement directory must not be a symlink')
    output.parent.mkdir(parents=True, exist_ok=True)
    if not output.parent.resolve().is_relative_to(measurement_root):
        raise ValueError('output parent escapes measurement directory')
    fd = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o700)
    os.close(fd)
    try:
        subprocess.run(
            ['objcopy', *[f'--remove-section={s}' for s in sections], str(source), str(output)],
            check=True,
            timeout=180,
        )
        if load_segments(output) != before_segments:
            raise ValueError('ELF load image or program headers changed')
        if file_sha256(source) != before_hash:
            raise ValueError('source binary changed during artifact creation')
        return {
            'source': str(source),
            'source_sha256': before_hash,
            'source_bytes': source.stat().st_size,
            'output': str(output),
            'output_sha256': file_sha256(output),
            'output_bytes': output.stat().st_size,
            'removed_non_alloc_debug_sections': sections,
            'load_image_and_program_headers_equal': True,
            'limitation': (
                'load-image equality and focused tests are not exhaustive '
                'behavioral equivalence'
            ),
        }
    except BaseException:
        output.unlink(missing_ok=True)
        raise
