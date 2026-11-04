"""Corpus and artifact identity helpers for the Rust lifecycle pair."""
from __future__ import annotations

import hashlib
import os
import pathlib
import subprocess
from typing import Any

from . import config  # ty: ignore[unresolved-import]


def validate_full_corpus_reference(workload: dict[str, Any]) -> None:
    """Reject an unprovenanced full-corpus reference unless it is explicitly blocked."""
    reference = workload.get("full_corpus_reference")
    if not reference:
        return
    if reference.get("provenance_status") == "blocked_missing_source":
        return
    missing = [
        key
        for key in ("source_repo", "source_commit", "source_selection")
        if not reference.get(key)
    ]
    if missing:
        raise ValueError("full corpus reference lacks provenance: " + ", ".join(missing))


def capture_pair_artifacts(
    binary: pathlib.Path, library_path: pathlib.Path, file_sha256
) -> dict[str, str]:
    binary = binary.resolve(strict=True)
    library_path = library_path.resolve(strict=True)
    library = (library_path / "libzvec_c_api.so").resolve(strict=True)
    if library.parent != library_path or not binary.is_file() or not library.is_file():
        raise ValueError("binary or native library path is not an exact regular artifact")
    return {
        "binary_path": str(binary),
        "binary_sha256": file_sha256(binary),
        "library_path": str(library_path),
        "library_file": str(library),
        "library_sha256": file_sha256(library),
    }


def verify_pair_artifacts(expected: dict[str, str], file_sha256) -> None:
    actual = capture_pair_artifacts(
        pathlib.Path(expected["binary_path"]), pathlib.Path(expected["library_path"]), file_sha256
    )
    if actual != expected:
        raise ValueError(
            "pair binary or native library identity changed; refusing continuation"
        )


def git(*args: str) -> bytes:
    source_repo = config.WORKLOAD.get('source_repo', str(config.REPO))
    return subprocess.run(['git', *args], cwd=source_repo, check=True, stdout=subprocess.PIPE).stdout


def frozen_manifest() -> tuple[list[tuple[str, str, int]], str]:
    validate_full_corpus_reference(config.WORKLOAD)
    commit = config.WORKLOAD['source_commit']
    tree = git('ls-tree', '-rlz', commit)
    entries: list[tuple[str, str, int]] = []
    selection = config.WORKLOAD.get('source_selection')
    for item in tree.split(b'\0'):
        if not item:
            continue
        meta, raw_path = item.split(b'\t', 1)
        _mode, kind, blob, size = meta.decode('ascii').split()
        path = raw_path.decode('utf-8')
        suffix = pathlib.PurePosixPath(path).suffix
        if selection is None:
            included = suffix in config.CODE_EXTENSIONS
        else:
            included = (
                path.startswith(selection['path_prefix']) and
                suffix in set(selection['extensions'])
            )
        if kind == 'blob' and included:
            entries.append((path, blob, int(size)))
    entries.sort()
    if selection is not None:
        entries = entries[:selection['max_files']]
    encoded = ''.join(f'{path}\t{blob}\t{size}\n' for path, blob, size in entries).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    if (len(entries) != config.WORKLOAD['source_file_count'] or
            sum(entry[2] for entry in entries) != config.WORKLOAD['source_bytes'] or
            digest != config.WORKLOAD['source_manifest_sha256']):
        raise ValueError('committed source corpus differs from the frozen Gate B manifest')
    return entries, digest


def is_executable(path: pathlib.Path) -> bool:
    try:
        return os.access(path, os.X_OK)
    except OSError:
        return False


def find_test_binary(binary_override: pathlib.Path | None = None,
                     library_override: pathlib.Path | None = None) -> tuple[pathlib.Path, pathlib.Path]:
    if binary_override is None:
        candidates = sorted((config.RUST_TARGET_DIR / 'debug/deps').glob('zg_engine-*'))
    else:
        binary = binary_override.resolve(strict=True)
        deps = (config.RUST_TARGET_DIR / 'debug/deps').resolve(strict=True)
        measurement_dir = deps / '.m4-measurement'
        allowed = measurement_dir.resolve(strict=True)
        if (measurement_dir.is_symlink() or allowed.parent != deps or
                not binary.is_relative_to(allowed) or not binary.name.startswith('zg_engine-') or
                not binary.name.endswith('-nodebug') or not binary.is_file() or not is_executable(binary)):
            raise ValueError('opt-in binary must be a nodebug executable in the measurement artifact directory')
        original = deps / binary.name.removesuffix('-nodebug')
        if (not original.is_file() or original.is_symlink() or original.resolve().parent != deps or
                config.measurement_artifact.load_segments(original) != config.measurement_artifact.load_segments(binary)):
            raise ValueError('measurement binary differs from its original ELF load image')
        candidates = [binary]
    matches: list[tuple[pathlib.Path, pathlib.Path]] = []
    build_root = (config.RUST_TARGET_DIR / 'debug/build').resolve(strict=True)
    if library_override is None:
        libs = sorted(build_root.glob('zvec-rust-sys-*/out/zvec-prebuilt/libzvec_c_api.so'))
        if not libs:
            raise FileNotFoundError('compiled libzvec_c_api.so is missing; build before Gate B validation')
        library_dirs = [libs[-1].parent.resolve(strict=True)]
    else:
        if library_override.is_symlink():
            raise ValueError('native library override must not be a symlink')
        library_dir = library_override.resolve(strict=True)
        library_file = library_dir / 'libzvec_c_api.so'
        if (not library_dir.is_relative_to(build_root) or not library_dir.is_dir() or
                library_file.is_symlink() or not library_file.is_file() or
                library_file.resolve(strict=True).parent != library_dir):
            raise ValueError('native library override must be an exact artifact under debug/build')
        library_dirs = [library_dir]
    for binary in candidates:
        if not binary.is_file() or not is_executable(binary):
            continue
        lib = library_dirs[0]
        env = {**os.environ, 'LD_LIBRARY_PATH': str(lib)}
        listing = subprocess.run([str(binary), '--list'], cwd=config.REPO, env=env, capture_output=True, text=True)
        if listing.returncode == 0 and any(line.startswith(config.TEST_NAME + ': test') for line in listing.stdout.splitlines()):
            matches.append((binary.resolve(), lib.resolve()))
    if not matches:
        raise ValueError('no compiled Gate B test binary found')
    matches.sort(key=lambda item: (item[0].stat().st_mtime_ns, str(item[0])))
    if (len(matches) > 1 and
            matches[-1][0].stat().st_mtime_ns == matches[-2][0].stat().st_mtime_ns):
        raise ValueError('multiple Gate B test binaries have the same newest build timestamp')
    # Cargo can leave older feature/build variants beside the current test binary.
    # Select the newest binary, then bind its exact SHA-256 into both arm contexts.
    return matches[-1]


def make_context(operation_id: str, operation_root: pathlib.Path, arm_role: str,
                 execution_order: list[str], artifacts: dict[str, str],
                 workspace_root: pathlib.Path, source_root: pathlib.Path,
                 quota_proof: dict[str, Any], operation_mode: str = 'pair',
                 query_interval_ms: int | None = None) -> dict[str, Any]:
    arm_name = 'pair-first' if arm_role == execution_order[0] else 'pair-second'
    if operation_mode == 'stage_a_rate_ladder':
        authorization = config.CONTRACT['stage_a_rate_ladder']
    elif operation_mode == 'stage_c_capacity_attribution':
        authorization = config.CONTRACT['stage_c_capacity_attribution']
    elif operation_mode == 'stage_e2_larger_corpus':
        authorization = config.CONTRACT['stage_e2_larger_corpus']
    elif operation_mode == 'stage_e3_camera_421':
        authorization = config.CONTRACT['stage_e3_camera_421']
    elif operation_mode == 'stage_e4_camera_300':
        authorization = config.CONTRACT['stage_e4_camera_300']
    elif operation_mode == 'native_query_stage_diagnostic':
        authorization = config.CONTRACT['native_query_stage_diagnostic']
    else:
        authorization = config.CONTRACT
    arm_roles = authorization.get('arm_roles', config.CONTRACT['arm_roles'])
    index_root = workspace_root / 'index'
    report_path = workspace_root / 'gate-b-report.json'
    temporary_directory = workspace_root / 'tmp'
    if operation_mode == 'stage_a_rate_ladder':
        resource_basis = (
            'one quota-contained Stage A rate-ladder pair on the 64-file pilot; '
            'no full corpus, no production, unchanged cgroup/qgroup caps'
        )
    elif operation_mode == 'stage_c_capacity_attribution':
        resource_basis = (
            'one quota-contained Stage C capacity-attribution pair on the 64-file pilot; '
            'no full corpus, no production, unchanged cgroup/qgroup caps'
        )
    elif operation_mode == 'stage_e2_larger_corpus':
        resource_basis = (
            'one quota-contained larger-corpus diagnostic on the 219-file Rust tree; '
            'not full-corpus Gate B, no production, unchanged cgroup/qgroup caps'
        )
    elif operation_mode == 'stage_e3_camera_421':
        resource_basis = (
            'one quota-contained 421-file camera/AttendanceSafetyCamera diagnostic; '
            'not full-corpus Gate B, no production, unchanged cgroup/qgroup caps'
        )
    elif operation_mode == 'stage_e4_camera_300':
        resource_basis = (
            'one quota-contained 300-file camera/AttendanceSafetyCamera diagnostic; '
            'not full-corpus Gate B, no production, unchanged 512 MiB envelope'
        )
    elif operation_mode == 'native_query_stage_diagnostic':
        resource_basis = (
            'one quota-contained native-only Gate B query-stage diagnostic; '
            'no M4 arm or pair comparison'
        )
    else:
        resource_basis = (
            'one quota-contained matched Gate B pair with preserved 18.219 GB disk '
            'reserve and enforced cgroup memory/swap/cpu/pids ceilings'
        )
    context: dict[str, Any] = {
        'schema_version': 1,
        'approval_status': 'approved',
        'mode': config.containment.CONTENTION_MODE,
        'policy_sha256': hashlib.sha256(config.containment.POLICY_PATH.read_bytes()).hexdigest(),
        'resource_basis': resource_basis,
        'purpose': config.containment.M4_RUST_LIFECYCLE_PURPOSE,
        'operation_mode': operation_mode,
        'operation_id': operation_id,
        'operation_root': str(operation_root),
        'approval_reference': authorization['approval_reference'],
        'execution_authorization_reference': authorization['execution_authorization_reference'],
        'risk_acceptance_reference': authorization['risk_acceptance_reference'],
        'workload': config.WORKLOAD,
        'query_filter': config.WORKLOAD['query_filter'],
        'corpus_extension': config.WORKLOAD.get('corpus_extension', '.rs'),
        'reader_query_interval_ms': (
            query_interval_ms if query_interval_ms is not None
            else config.WORKLOAD['reader_query_interval_ms']),
        'reader_query_max_samples_per_pass': config.WORKLOAD['reader_query_max_samples_per_pass'],
        'reader_query_queue_capacity': config.WORKLOAD['reader_query_queue_capacity'],
        'zvec_native_memory_limit_bytes': config.WORKLOAD['zvec_native_memory_limit_bytes'],
        'arm_roles': arm_roles,
        'arm_role': arm_role,
        'execution_order': execution_order,
        'resource_root': config.CONTRACT['resource_root'],
        'workspace_root': str(workspace_root),
        'source_root': str(source_root),
        'index_root': str(index_root),
        'report_path': str(report_path),
        'temporary_directory': str(temporary_directory),
        'binary_path': artifacts['binary_path'],
        'binary_name': pathlib.Path(artifacts['binary_path']).name,
        'binary_sha256': artifacts['binary_sha256'],
        'approved_binary_sha256': artifacts['binary_sha256'],
        'library_path': artifacts['library_path'],
        'library_file': artifacts['library_file'],
        'library_sha256': artifacts['library_sha256'],
        'approved_library_sha256': artifacts['library_sha256'],
        'io_pressure_mode': config.CONTRACT['io_pressure_mode'],
        'workspace_quota_proof': quota_proof,
        **config.CONTRACT['resource_controls'],
    }
    context['arm_name'] = arm_name
    if operation_mode == 'native_query_stage_diagnostic':
        context['native_profile_trace_path'] = str(
            operation_root / arm_name / 'containment' / 'native-profile.jsonl')
    return context


def build_environment(context: dict[str, Any]) -> dict[str, str]:
    return config.containment.rust_lifecycle_expected_environment(context)


def test_command(context: dict[str, Any]) -> list[str]:
    return [context['binary_path'], '--exact', config.TEST_NAME, '--ignored', '--nocapture']
