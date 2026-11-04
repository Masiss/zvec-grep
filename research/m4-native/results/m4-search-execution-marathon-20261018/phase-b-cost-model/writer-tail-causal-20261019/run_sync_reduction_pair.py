#!/usr/bin/env python3
"""Resource-gated 500x20 same-candidate pair: redundant-FTS-sync treatment vs control."""
import csv
import hashlib
import importlib.util
import json
import os
import pathlib
import re
import stat
import subprocess
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[5]
CONTAINMENT_PATH = REPO_ROOT / "research/m4-native/tools/m4_containment_runner.py"
_containment_spec = importlib.util.spec_from_file_location("m4_containment_runner", CONTAINMENT_PATH)
if _containment_spec is None or _containment_spec.loader is None:
    raise RuntimeError(f"cannot import containment runner: {CONTAINMENT_PATH}")
containment = importlib.util.module_from_spec(_containment_spec)
_containment_spec.loader.exec_module(containment)
PAIR_LABEL = os.environ.get("M4_SYNC_PAIR_LABEL", "sync-reduction")
PAIR = HERE / f"{PAIR_LABEL}-pair-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
EXPECTED_AA_IDENTITY = None
BENCHMARK_POLICY = containment.BENCHMARK_POLICY
RESOURCE_ROOT = pathlib.Path(os.environ["M4_RESOURCE_ROOT"]).resolve(strict=True)
CONTROL = pathlib.Path(os.environ["M4_SYNC_CONTROL_BINARY"]).resolve(strict=True)
TREATMENT = pathlib.Path(os.environ["M4_SYNC_TREATMENT_BINARY"]).resolve(strict=True)
DICT = pathlib.Path.cwd() / ".local/native/zvec-native-profile/thirdparty/cppjieba/cppjieba-5.6.7/dict"
THRESHOLDS = dict(BENCHMARK_POLICY["resource_guards"])
DESCRIPTION = os.environ.get("M4_SYNC_DESCRIPTION",
    "research-only fsync-elision diagnostic; WAL truncate/recovery equivalence unproven")
ORDER = os.environ.get("M4_SYNC_PAIR_ORDER", "reduced-first")
if ORDER == "reduced-first":
    ARMS = [("sync-reduced", TREATMENT), ("sync-control", CONTROL)]
elif ORDER == "control-first":
    ARMS = [("sync-control", CONTROL), ("sync-reduced", TREATMENT)]
else:
    raise SystemExit(f"unsupported M4_SYNC_PAIR_ORDER: {ORDER}")


def parse_number(value, cast):
    try:
        return cast(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"invalid host resource value: {value!r}") from exc


def read_json_file(path, description):
    try:
        return json.loads(pathlib.Path(path).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {description}: {path}") from exc


def read_meminfo():
    return {line.split(":", 1)[0]: parse_number(line.split()[1].split()[0], int)
            for line in pathlib.Path("/proc/meminfo").read_text().splitlines() if ":" in line}


def read_vmstat_swap():
    wanted = {"pswpin", "pswpout"}
    return {key: parse_number(value, int)
            for key, value in (line.split() for line in pathlib.Path("/proc/vmstat").read_text().splitlines())
            if key in wanted}


def read_diskstats():
    result = {}
    for line in pathlib.Path("/proc/diskstats").read_text().splitlines():
        fields = line.split()
        if len(fields) >= 14:
            result[fields[2]] = {
                "reads_completed": parse_number(fields[3], int),
                "sectors_read": parse_number(fields[5], int),
                "read_ms": parse_number(fields[6], int),
                "writes_completed": parse_number(fields[7], int),
                "sectors_written": parse_number(fields[9], int),
                "write_ms": parse_number(fields[10], int),
                "in_flight": parse_number(fields[11], int),
                "io_ms": parse_number(fields[12], int),
            }
    return result


def binary_runtime_identity(binary):
    binary = pathlib.Path(binary).resolve(strict=True)
    files = [REPO_ROOT / "research/m4-native/CMakeLists.txt"]
    for directory in (REPO_ROOT / "research/m4-native/include", REPO_ROOT / "research/m4-native/src"):
        files.extend(p for p in directory.rglob("*") if p.is_file())
    files.append(REPO_ROOT / "research/m4-native/tests/m4_bridge_integration.cc")
    source_hashes = {str(p.relative_to(REPO_ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in sorted(set(files)) if p.is_file()}
    tree = hashlib.sha256()
    for path, digest in sorted(source_hashes.items()):
        tree.update(path.encode() + b"\x00" + digest.encode() + b"\n")
    runner_files = [
        HERE / "run_host_carryover_aa.py", HERE / "run_sync_reduction_pair.py",
        HERE / "capture_host_baseline.py", HERE / "analyze_host_pressure.py",
        HERE / "summarize_aa_diagnostic.py", CONTAINMENT_PATH, containment.POLICY_PATH,
    ]
    runner_hashes = {str(p.relative_to(REPO_ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in sorted(set(runner_files)) if p.is_file()}
    runner_tree = hashlib.sha256()
    for path, digest in sorted(runner_hashes.items()):
        runner_tree.update(path.encode() + b"\x00" + digest.encode() + b"\n")
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT,
                              capture_output=True, text=True, check=True).stdout.strip()
    ldd = subprocess.run(["ldd", str(binary)], capture_output=True, text=True, check=False)
    libraries = {}
    for line in ldd.stdout.splitlines():
        parts = line.strip().split()
        candidate = parts[2] if len(parts) >= 3 and parts[1] == "=>" else parts[0] if parts and parts[0].startswith("/") else None
        if candidate and pathlib.Path(candidate).is_file():
            resolved = pathlib.Path(candidate).resolve(strict=True)
            libraries[str(resolved)] = hashlib.sha256(resolved.read_bytes()).hexdigest()
    return {
        "binary_realpath": str(binary),
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "source_git_revision": revision,
        "current_source_tree_sha256": tree.hexdigest(),
        "current_source_file_sha256": source_hashes,
        "measurement_runner_tree_sha256": runner_tree.hexdigest(),
        "measurement_runner_file_sha256": runner_hashes,
        "loaded_shared_libraries": libraries,
        "ldd_returncode": ldd.returncode,
        "ldd_output": "\n".join(re.sub(r"\s+\(0x[0-9a-fA-F]+\)$", "", line)
                                      for line in (ldd.stdout + ldd.stderr).splitlines()).strip(),
    }


def verify_correctness_durability(fault, binary_sha256, correctness_exit, fault_tests_exit):
    """Reject a failed or mismatched retained correctness/durability prerequisite."""
    if (fault.get('status') != 'PASS' or fault.get('binary_sha256') != binary_sha256 or
            correctness_exit.strip() != '0' or fault_tests_exit.strip() != '0'):
        raise ValueError('correctness/durability prerequisite did not pass')


def verify_full_sync(binary):
    """Static verification only: never run the benchmark or redo completed correctness tests."""
    archive = REPO_ROOT / 'research/m4-native/results/durability-restore-20261020'
    manifest = read_json_file(archive / 'build-provenance.json', 'build provenance')
    identity = binary_runtime_identity(binary)
    if (identity['binary_sha256'] != BENCHMARK_POLICY['contention_aware']['full_sync_binary_sha256'] or
            identity['binary_sha256'] != manifest['binary_sha256'] or
            identity['ldd_returncode'] != 0 or
            identity['loaded_shared_libraries'] != manifest['runtime_library_hashes']):
        raise ValueError('full-sync binary/runtime provenance mismatch')
    for name, expected in manifest['compile_dependency_hashes'].items():
        if hashlib.sha256((REPO_ROOT / name).read_bytes()).hexdigest() != expected:
            raise ValueError(f'compiler input mismatch: {name}')
    if len(manifest['compile_dependency_hashes']) != manifest['compile_dependency_count']:
        raise ValueError('incomplete compiler dependency manifest')
    for line in (archive / 'SHA256SUMS.txt').read_text().splitlines():
        expected, name = line.split(maxsplit=1)
        if hashlib.sha256((archive / name.lstrip('*')).read_bytes()).hexdigest() != expected:
            raise ValueError(f'durability evidence changed: {name}')
    fault = read_json_file(archive / 'faults-corrected/summary.json', 'correctness/durability receipt')
    verify_correctness_durability(
        fault, identity['binary_sha256'],
        (archive / 'correctness-exit.txt').read_text(),
        (archive / 'fault-tests-corrected-exit.txt').read_text(),
    )
    return {'build_manifest': manifest, 'runtime_identity': identity,
            'durability_evidence': fault, 'verification': 'static hashes and retained completed tests; no new binary execution'}


def read_host(policy_context=None):
    state = containment.host_snapshot(RESOURCE_ROOT)
    mem = state.get("meminfo_kib", {})
    load = state.get("loadavg") or []
    io10 = ((state.get("io_pressure") or {}).get("full") or {}).get("avg10")
    package = subprocess.run(["pgrep", "-a", "-f", "(^|/)(pacman|yay)( |$)"], text=True, capture_output=True)
    try:
        memory_psi = pathlib.Path("/proc/pressure/memory").read_text()
    except OSError:
        memory_psi = None
    snapshot = {"time": state["timestamp_utc"],
            "MemAvailable_kib": mem.get("MemAvailable_kib"),
            "load1": load[0] if load else None, "io_full_avg10": io10,
            "resource_root_available_bytes": state["resource_root_free_bytes"],
            "memory_psi": memory_psi, "swap_kib": {k: v for k, v in mem.items()
                if k in ("SwapTotal_kib", "SwapFree_kib")},
            "vmstat_swap_pages": read_vmstat_swap(), "diskstats": read_diskstats(),
            "resource_root_inode_report": subprocess.check_output(["df", "-Pi", str(RESOURCE_ROOT)], text=True).splitlines()[-1],
            "package_manager_processes": package.stdout.splitlines(),
            "top_processes": subprocess.check_output(["ps", "-eo", "pid,comm,%cpu,%mem,rss", "--sort=-%cpu"], text=True).splitlines()[:13]}
    if policy_context is not None:
        snapshot["contention_host_snapshot"] = state
    return snapshot


def preflight(arm, policy_context=None):
    out = PAIR / arm
    out.mkdir(parents=True, exist_ok=policy_context is None)
    try:
        host = read_host(policy_context) if policy_context is not None else read_host()
        diagnostic_decision = (containment.contention_decision(host['contention_host_snapshot'], policy_context, arm)
                               if policy_context is not None else None)
    except Exception as exc:
        failure = {
            "status": "TELEMETRY_INCOMPLETE", "operation_outcome": "OPERATION_BLOCKED",
            "gate_class": "MEASUREMENT_INTEGRITY", "reason": f"{type(exc).__name__}: {exc}",
            "benchmark_launched": False, "failure_scope": "this arm only",
            "mission_status": "NOT_EVALUATED_BY_RUNNER", "policy_id": BENCHMARK_POLICY["policy_id"],
            "source_of_authority": "host resource telemetry required by the current operation policy",
            "recovery": "restore complete host telemetry and rerun preflight only when authorized; no automatic retry",
        }
        (out / "host-before.json").write_text(json.dumps(failure, indent=2) + "\n")
        (out / "preflight.json").write_text(json.dumps(failure, indent=2) + "\n")
        (out / "no-launch.json").write_text(json.dumps(failure, indent=2) + "\n")
        print(f"OPERATION_BLOCKED phase={arm} gate=MEASUREMENT_INTEGRITY reason={failure['reason']}",
              file=sys.stderr, flush=True)
        return False
    host["thresholds"] = THRESHOLDS
    host["diagnostic_decision"] = diagnostic_decision
    host["policy_context"] = policy_context
    policy_state = {
        "meminfo_kib": {"MemAvailable_kib": host.get("MemAvailable_kib")},
        "resource_root_free_bytes": host.get("resource_root_available_bytes"),
        "io_pressure": {"full": {"avg10": host.get("io_full_avg10")}},
        "loadavg": [host.get("load1")],
    }
    gate_decisions = containment.resource_guard_decisions(policy_state)
    failures = [{"metric": decision["threshold_key"], "observed": decision["observed"],
                 "threshold": decision["threshold"], "gate_id": decision["gate_id"],
                 "evidence_class": decision["evidence_class"]}
                for decision in gate_decisions
                if decision["status"] == "FAIL" and decision.get("enforced", False)]
    host["gate_decisions"] = gate_decisions
    host["resource_guard_failures"] = failures
    host["hard_safety_failures"] = failures  # compatibility field; policy class/source are explicit above
    if diagnostic_decision is not None:
        failures = diagnostic_decision['blocking_failures']
        host['hard_safety_failures'] = failures
    host["policy_id"] = BENCHMARK_POLICY["policy_id"]
    host["source_of_authority"] = BENCHMARK_POLICY["authority"]["historical_envelope_source"]
    host["failure_scope"] = "this arm or benchmark operation only"
    host["mission_status"] = "NOT_EVALUATED_BY_RUNNER"
    host["operation_outcome"] = "OPERATION_BLOCKED" if failures else "OPERATION_READY"
    host["pass"] = not failures
    host["decision"] = (('OPERATION_READY_EXPLORATORY' if host['pass'] else 'CONTENTION_CAPACITY_BLOCKED')
                        if policy_context is not None else 'PASS' if host['pass'] else 'OPERATION_GUARD_FAIL')
    host["benchmark_launched"] = False
    (out / "host-before.json").write_text(json.dumps(host, indent=2) + "\n")
    (out / "preflight.json").write_text(json.dumps({
        "status": host["decision"], "operation_outcome": host["operation_outcome"],
        "failure_scope": host["failure_scope"], "mission_status": host["mission_status"],
        "policy_id": BENCHMARK_POLICY["policy_id"],
        "source_of_authority": host["source_of_authority"],
        "gate_decisions": gate_decisions, "resource_guard_failures": host['resource_guard_failures'],
        "diagnostic_decision": diagnostic_decision,
        "evidence_class": containment.CONTENTION_EVIDENCE if policy_context is not None else None,
        "hard_safety_failures": failures, "thresholds": THRESHOLDS,
        "recovery": "fresh snapshot satisfies every unchanged resource guard; no automatic retry",
        "resource_snapshot": host,
    }, indent=2) + "\n")
    if not host["pass"]:
        (out / "no-launch.json").write_text(json.dumps(host, indent=2) + "\n")
        print(f"OPERATION_GUARD_FAIL phase={arm}: {host}", file=sys.stderr, flush=True)
        return False
    return True


def sample_pss(proc, path, runtime_path, pressure_limits=None):
    # Sample host pressure and process I/O alongside PSS so cycle-tail spikes
    # can be distinguished from second-arm/carry-over effects without LD_PRELOAD.
    # Keep all output in memory until the child exits; hot-path file writes could
    # otherwise add writeback activity to the workload being measured.
    pss_rows = []
    runtime_rows = []
    resource_guard_violations = []
    runtime_fields = [
        "monotonic_ns", "sample_start_monotonic_ns", "sample_duration_ns", "pid",
        "read_bytes", "write_bytes", "syscr", "syscw", "memavailable_kib", "swapfree_kib",
        "pswpin_pages", "pswpout_pages", "io_some_avg10", "io_some_total_us",
        "io_full_avg10", "io_full_total_us", "memory_some_avg10", "memory_some_total_us",
        "memory_full_avg10", "memory_full_total_us", "diskstats_json",
        "load1", "resource_root_available_bytes", "process_cpu_ticks",
    ]
    try:
        while proc.poll() is None:
            try:
                sample_start = time.monotonic_ns()
                fields = {}
                for line in pathlib.Path(f"/proc/{proc.pid}/smaps_rollup").read_text().splitlines():
                    key, value, *_ = line.split()
                    if key in ("Rss:", "Pss:", "Swap:"):
                        fields[key] = parse_number(value, int)
                stat_text = pathlib.Path(f"/proc/{proc.pid}/stat").read_text()
                stat_tail = stat_text[stat_text.rfind(")") + 2:].split()
                process_cpu_ticks = int(stat_tail[11]) + int(stat_tail[12])
                io_fields = {}
                for line in pathlib.Path(f"/proc/{proc.pid}/io").read_text().splitlines():
                    key, value = line.split(":", 1)
                    io_fields[key] = parse_number(value.strip(), int)
                pressures = {}
                for kind, filename in (("io", "io"), ("memory", "memory")):
                    for line in pathlib.Path(f"/proc/pressure/{filename}").read_text().splitlines():
                        parts = line.split()
                        if parts[0] in ("some", "full"):
                            values = dict(item.split("=", 1) for item in parts[1:])
                            pressures[f"{kind}_{parts[0]}_avg10"] = values.get("avg10", "NA")
                            pressures[f"{kind}_{parts[0]}_total_us"] = values.get("total", "NA")
                meminfo = read_meminfo()
                swap_pages = read_vmstat_swap()
                diskstats = read_diskstats()
                load1 = parse_number(pathlib.Path("/proc/loadavg").read_text().split()[0], float)
                statvfs = os.statvfs(RESOURCE_ROOT)
                resource_free = statvfs.f_bavail * statvfs.f_frsize
                now = time.monotonic_ns()
                pss_rows.append([now, proc.pid, fields.get("Rss:", 0), fields.get("Pss:", 0), fields.get("Swap:", 0)])
                runtime_rows.append([
                    now, sample_start, now - sample_start, proc.pid,
                    io_fields.get("read_bytes", 0), io_fields.get("write_bytes", 0),
                    io_fields.get("syscr", 0), io_fields.get("syscw", 0),
                    meminfo.get("MemAvailable", 0), meminfo.get("SwapFree", 0),
                    swap_pages.get("pswpin", 0), swap_pages.get("pswpout", 0),
                    pressures.get("io_some_avg10", "NA"), pressures.get("io_some_total_us", "NA"),
                    pressures.get("io_full_avg10", "NA"), pressures.get("io_full_total_us", "NA"),
                    pressures.get("memory_some_avg10", "NA"), pressures.get("memory_some_total_us", "NA"),
                    pressures.get("memory_full_avg10", "NA"), pressures.get("memory_full_total_us", "NA"),
                    json.dumps(diskstats, separators=(",", ":")), load1, resource_free, process_cpu_ticks,
                ])
                # Host MemAvailable, PSI and load are context telemetry only.
                # Keep actual disk-reserve and approved swap-capacity stops.
                io_full = float(pressures.get("io_full_avg10", "inf"))
                if (resource_free < THRESHOLDS["var_tmp_bytes_min"] or
                        (pressure_limits is not None and
                         meminfo.get("SwapFree", 0) < pressure_limits["swapfree_min_kib"])):
                    policy_state = {
                        "meminfo_kib": {"MemAvailable_kib": meminfo.get("MemAvailable")},
                        "resource_root_free_bytes": resource_free,
                        "io_pressure": {"full": {"avg10": io_full}}, "loadavg": [load1],
                    }
                    gate_decisions = containment.resource_guard_decisions(policy_state)
                    resource_guard_violations.append({
                        "operation_outcome": "OPERATION_BLOCKED", "failure_scope": "current arm only",
                        "mission_status": "NOT_EVALUATED_BY_RUNNER", "gate_decisions": gate_decisions,
                        "baseline_pressure_failures": containment.pressure_baseline_failures({
                            "meminfo_kib": {"SwapFree_kib": meminfo.get("SwapFree")},
                        }, pressure_limits),
                        "monotonic_ns": now, "MemAvailable_kib": meminfo.get("MemAvailable", 0),
                        "resource_root_available_bytes": resource_free, "io_full_avg10": io_full,
                        "load1": load1, "swapfree_kib": meminfo.get("SwapFree", 0),
                        "memory_some_avg10": pressures.get("memory_some_avg10"),
                        "memory_full_avg10": pressures.get("memory_full_avg10"),
                        "pressure_limits": pressure_limits, "thresholds": THRESHOLDS,
                    })
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
                    break
            except (FileNotFoundError, ProcessLookupError):
                break
            try:
                proc.wait(timeout=0.05)
                break
            except subprocess.TimeoutExpired:
                continue
    finally:
        with pathlib.Path(path).open("w", newline="") as out:
            pss_csv = csv.writer(out)
            pss_csv.writerow(["monotonic_ns", "pid", "rss_kb", "pss_kb", "swap_kb"])
            pss_csv.writerows(pss_rows)
        with pathlib.Path(runtime_path).open("w", newline="") as runtime_file:
            runtime_csv = csv.writer(runtime_file)
            runtime_csv.writerow(runtime_fields)
            runtime_csv.writerows(runtime_rows)
    return resource_guard_violations


def write_containment_compatibility_samples(out, receipt_dir):
    runtime_rows = []
    pss_rows = []
    for line_number, line in enumerate((receipt_dir / "runtime.jsonl").read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            sample = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid runtime telemetry JSON at line {line_number}") from exc
        host = sample["host"]
        cg = sample["cgroup"]
        mem = host["meminfo_kib"]
        vm = host["vmstat"]
        io = host.get("io_pressure") or {}
        memory = host.get("memory_pressure") or {}
        io_some, io_full = io.get("some") or {}, io.get("full") or {}
        memory_some, memory_full = memory.get("some") or {}, memory.get("full") or {}
        loadavg = host.get("loadavg") or []
        cpu = cg["cpu_stat"].get("usage_usec", "NA")
        runtime_rows.append({
            "monotonic_ns": sample["monotonic_ns"],
            "process_cpu_ticks": "NA", "cgroup_cpu_usage_usec": cpu,
            "read_bytes": cg["process_tree_read_bytes"],
            "write_bytes": cg["process_tree_write_bytes"],
            "process_io_sample_complete": cg["process_io_sample_complete"],
            "memavailable_kib": mem.get("MemAvailable_kib", "NA"),
            "swapfree_kib": mem.get("SwapFree_kib", "NA"),
            "pswpin_pages": vm.get("pswpin", "NA"), "pswpout_pages": vm.get("pswpout", "NA"),
            "load1": loadavg[0] if loadavg else None,
            "io_some_avg10": io_some.get("avg10"), "io_full_avg10": io_full.get("avg10"),
            "io_some_total_us": io_some.get("total"), "io_full_total_us": io_full.get("total"),
            "memory_some_avg10": memory_some.get("avg10"), "memory_full_avg10": memory_full.get("avg10"),
            "memory_some_total_us": memory_some.get("total"), "memory_full_total_us": memory_full.get("total"),
            "diskstats_json": json.dumps(host["diskstats"], sort_keys=True),
            "cgroup_memory_current_bytes": cg["memory_current_bytes"],
            "cgroup_memory_peak_bytes": cg["memory_peak_bytes"],
            "cgroup_memory_swap_current_bytes": cg["memory_swap_current_bytes"],
            "cgroup_processes": json.dumps(cg["pids"]),
        })
        pss_rows.append({
            "monotonic_ns": sample["monotonic_ns"], "pid": json.dumps(cg["pids"]),
            "rss_kb": cg["process_tree_rss_kib"], "pss_kb": cg["process_tree_pss_kib"],
            "swap_kb": cg["process_tree_swap_kib"],
            "sample_complete": cg["process_memory_sample_complete"],
        })
    if not runtime_rows:
        raise RuntimeError(f"containment runner produced no runtime samples: {receipt_dir}")
    for name, rows in (("runtime-samples.csv", runtime_rows), ("pss-samples.csv", pss_rows)):
        with (out / name).open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)


def run_arm(name, binary, mode="qualification", pressure_limits=None, policy_context=None):
    if (mode == containment.CONTENTION_MODE) != (policy_context is not None):
        raise ValueError('contention mode and explicit operation context must agree')
    if policy_context is not None:
        containment.validate_contention_context(policy_context)
        if PAIR.resolve() != pathlib.Path(policy_context['operation_root']) or name not in ('aa-first', 'aa-second'):
            raise ValueError('outer arm contention operation binding mismatch')
        verify_full_sync(binary)
    if not (preflight(name, policy_context) if policy_context is not None else preflight(name)):
        return False
    out = PAIR / name
    identity = binary_runtime_identity(binary)
    (out / "binary-identity.json").write_text(json.dumps(identity, indent=2, sort_keys=True) + "\n")
    if EXPECTED_AA_IDENTITY is not None and identity != EXPECTED_AA_IDENTITY:
        (out / "identity-mismatch.json").write_text(json.dumps({
            "status": "IDENTITY_MISMATCH_NO_LAUNCH", "expected": EXPECTED_AA_IDENTITY,
            "observed": identity, "benchmark_launched": False,
        }, indent=2) + "\n")
        return False
    work = pathlib.Path(tempfile.mkdtemp(prefix="m4-writer-sync-pair-", dir=RESOURCE_ROOT))
    if stat.S_IMODE(work.stat().st_mode) != 0o700:
        raise RuntimeError(f"unexpected private workload mode: {work}")
    command = [str(binary), str(work / "root"), str(DICT), "--incremental-fts-sustained", "500", "20"]
    (out / "mode.json").write_text(json.dumps({
        "mode": mode,
        "evidence_class": (containment.CONTENTION_EVIDENCE if policy_context is not None else
                           "EXPLORATORY / NOT QUALIFICATION EVIDENCE" if mode == "diagnostic" else "QUALIFICATION"),
        "policy_context": policy_context,
        "operation_resource_guards": THRESHOLDS,
        "baseline_pressure_guard_limits": pressure_limits,
        "benchmark_policy_id": BENCHMARK_POLICY["policy_id"],
        "whole_mission_status": "NOT_EVALUATED_BY_RUNNER",
        "containment": "transient systemd user service; cgroup-v2 per arm",
        "io_control": "UNAVAILABLE unless the arm cgroup exposes io.stat/io.max; host diskstats and process-tree /proc IO are telemetry only",
    }, indent=2) + "\n")
    (out / "command.json").write_text(json.dumps({"argv": command, "cwd": str(pathlib.Path.cwd()),
        "private_workdir": str(work), "resource_root": str(RESOURCE_ROOT)}, indent=2) + "\n")
    (out / "binary-sha256.txt").write_text(subprocess.check_output(["sha256sum", str(binary)], text=True))
    env = os.environ.copy()
    for key in list(env):
        if key.startswith("M4_") or key == "LD_PRELOAD":
            env.pop(key)
    arm_start = {"monotonic_ns": time.monotonic_ns(), "epoch_ns": time.time_ns()}
    (out / "arm-start.json").write_text(json.dumps(arm_start, indent=2) + "\n")
    receipt_dir = out / "containment"
    result = containment.run_arm(
        command, receipt_dir, RESOURCE_ROOT, work, pathlib.Path.cwd(),
        runtime_seconds=(policy_context['runtime_limit_seconds'] if policy_context is not None else
                         float(os.environ.get("M4_AA_RUNTIME_LIMIT_SECONDS", "3600"))),
        sample_interval_ms=50,
        budget=containment.contention_budget(policy_context) if policy_context is not None else None, env=env,
        pressure_limits=pressure_limits, enforce_host_gates=True,
        **({'policy_context': policy_context} if policy_context is not None else {}),
    )
    for source, target in (("stdout.log", "stdout.jsonl"), ("stderr.log", "stderr.log")):
        src = receipt_dir / source
        if src.exists():
            (out / target).write_bytes(src.read_bytes())
    receipt_path = receipt_dir / "receipt.json"
    receipt = read_json_file(receipt_path, "arm receipt") if receipt_path.exists() else result
    if (receipt_dir / "runtime.jsonl").is_file():
        write_containment_compatibility_samples(out, receipt_dir)
    exit_code = receipt.get("workload_returncode")
    arm_end_ns = time.monotonic_ns()
    (out / "arm-end.json").write_text(json.dumps({
        "monotonic_ns": arm_end_ns, "epoch_ns": time.time_ns(), "exit_code": exit_code,
        "duration_seconds": (arm_end_ns - arm_start["monotonic_ns"]) / 1e9,
        "containment_status": receipt.get("status"),
        "operation_outcome": receipt.get("operation_outcome"),
        "failure_scope": receipt.get("failure_scope", "this arm only"),
        "mission_status": "NOT_EVALUATED_BY_RUNNER",
        "workload_started": receipt.get("benchmark_started"),
    }, indent=2) + "\n")
    (out / "exit-code.txt").write_text(f"{exit_code if exit_code is not None else 'NA'}\n")
    (out / "host-after.json").write_text(json.dumps(
        read_host(policy_context) if policy_context is not None else read_host(), indent=2) + "\n")
    operation_complete = (receipt.get("status") == "PASS" and exit_code == 0 and
                          receipt.get("measurement_quality") == "COMPLETE")
    performance_eligible = operation_complete and not receipt.get("test_fixture_mode", False) and policy_context is None
    (out / "arm-result.json").write_text(json.dumps({
        "status": receipt.get("status"), "failure_class": receipt.get("failure_class"),
        "operation_outcome": receipt.get("operation_outcome"),
        "failure_scope": receipt.get("failure_scope", "this arm only"),
        "mission_status": "NOT_EVALUATED_BY_RUNNER",
        "policy_id": receipt.get("policy_id", BENCHMARK_POLICY["policy_id"]),
        "safety_outcome": receipt.get("safety_outcome"),
        "measurement_quality": receipt.get("measurement_quality"),
        "test_fixture_mode": receipt.get("test_fixture_mode", False),
        "operation_complete": operation_complete,
        "performance_eligible": performance_eligible,
        "unit": receipt.get("unit"), "cgroup_relative_path": receipt.get("cgroup_relative_path"),
        "io_control": receipt.get("io_control"),
        "runtime_limit_seconds": (policy_context['runtime_limit_seconds'] if policy_context is not None else
                                  float(os.environ.get("M4_AA_RUNTIME_LIMIT_SECONDS", "3600"))),
        "evidence_class": containment.CONTENTION_EVIDENCE if policy_context is not None else None,
        "private_workdir": str(work), "workload_root": str(work / "root"),
    }, indent=2) + "\n")
    files = sorted(x for x in out.rglob("*") if x.is_file() and x.name != "SHA256SUMS.txt")
    with (out / "SHA256SUMS.txt").open("w") as sums:
        subprocess.run(["sha256sum", *map(str, files)], check=True, stdout=sums)
    return operation_complete


def write_pair_status(status, phase, *, reason=None, gate_id=None):
    if not PAIR.is_dir():
        return
    payload = {
        "operation_id": PAIR.name, "operation": "sustained writer A/B pair",
        "operation_status": status, "phase": phase, "reason": reason,
        "gate_id": gate_id, "failure_scope": "this benchmark pair only",
        "mission_status": "NOT_EVALUATED_BY_RUNNER", "mission_marked_done": False,
        "safe_independent_work_may_continue": status != "SAFETY_STOP",
        "policy_id": BENCHMARK_POLICY["policy_id"],
        "source_of_authority": BENCHMARK_POLICY["authority"]["historical_envelope_source"],
    }
    tmp = PAIR / ".operation-status.json.tmp"
    tmp.write_text(json.dumps(payload, indent=2) + "\n")
    tmp.replace(PAIR / "operation-status.json")


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("diagnostic", "qualification"), default="qualification")
    mode = parser.parse_args().mode
    if not RESOURCE_ROOT.is_dir() or not DICT.is_dir():
        raise SystemExit("resource root or Jieba dictionary is missing")
    for name, binary in ARMS:
        if not binary.is_file():
            raise SystemExit(f"missing {name} binary: {binary}")
    PAIR.mkdir(parents=True, exist_ok=False)
    (PAIR / "pair.json").write_text(json.dumps({"mode": mode,
        "evidence_class": "EXPLORATORY / NOT QUALIFICATION EVIDENCE" if mode == "diagnostic" else "QUALIFICATION",
        "order": [x[0] for x in ARMS], "order_mode": ORDER,
        "control": str(CONTROL), "treatment": str(TREATMENT), "workload": "500 docs x 20 rounds",
        "durability": f"no change; {DESCRIPTION}",
        "thresholds": THRESHOLDS, "policy_id": BENCHMARK_POLICY["policy_id"],
        "source_of_authority": BENCHMARK_POLICY["authority"]["historical_envelope_source"],
        "operation_status": "NOT_STARTED", "mission_status": "NOT_EVALUATED_BY_RUNNER"}, indent=2) + "\n")
    write_pair_status("IN_PROGRESS", "arm-pair")
    for name, binary in ARMS:
        if not run_arm(name, binary, mode):
            receipt_path = PAIR / name / "containment" / "receipt.json"
            preflight_path = PAIR / name / "preflight.json"
            receipt = read_json_file(receipt_path, "arm receipt") if receipt_path.is_file() else {}
            preflight_result = read_json_file(preflight_path, "arm preflight receipt") if preflight_path.is_file() else {}
            failed_gates = [g.get("gate_id") for g in receipt.get("gate_decisions", [])
                            if g.get("status") == "FAIL" and g.get("enforced", False)]
            failed_gates.extend(f.get("gate_id") for f in preflight_result.get("resource_guard_failures", [])
                                if f.get("gate_id"))
            no_start = not receipt.get("benchmark_started", False) and not preflight_result.get("benchmark_started", False)
            unverified_cleanup = receipt.get("safety_outcome") == "CONTAINMENT_UNVERIFIED"
            status = ("SAFETY_STOP" if unverified_cleanup else
                      "OPERATION_BLOCKED" if no_start or receipt.get("status") == "WATCHDOG_ABORT" else
                      "OPERATION_FAILED")
            reason = receipt.get("reason") or receipt.get("status") or preflight_result.get("reason") or preflight_result.get("status")
            write_pair_status(status, f"{name}-arm", reason=reason,
                              gate_id=failed_gates or preflight_result.get("gate_class"))
            raise SystemExit(2)
    write_pair_status("OPERATION_COMPLETE", "pair-complete")
    print(f"PAIR_COMPLETE {PAIR}")


def record_pair_exception(exc):
    current_path = PAIR / "operation-status.json"
    if not current_path.is_file():
        return
    current = read_json_file(current_path, "operation status receipt")
    if current.get("operation_status") not in {"IN_PROGRESS", "NOT_STARTED"}:
        return
    message = str(exc)
    receipts = []
    for path in PAIR.rglob("receipt.json"):
        try:
            receipts.append(json.loads(path.read_text()))
        except (OSError, json.JSONDecodeError):
            continue
    unverified = any(item.get("safety_outcome") == "CONTAINMENT_UNVERIFIED" for item in receipts)
    if unverified or "SAFETY_STOP" in message:
        status = "SAFETY_STOP"
    elif isinstance(exc, SystemExit) and ("NO_LAUNCH" in message or "STOP:" in message):
        status = "OPERATION_BLOCKED"
    else:
        status = "OPERATION_FAILED"
    write_pair_status(status, "uncaught-pair-exit", reason=message)


if __name__ == "__main__":
    try:
        main()
    except BaseException as exc:
        record_pair_exception(exc)
        raise
