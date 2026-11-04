#!/usr/bin/env python3
"""Resource-gated OFF/ON overhead check for low-overhead sync tracing v2."""
import json
import os
import pathlib
import stat
import subprocess
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent
ARTIFACT = HERE / f"diag-aa-v2-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
RESOURCE_ROOT = pathlib.Path(os.environ["M4_RESOURCE_ROOT"]).resolve(strict=True)
BIN = pathlib.Path(os.environ["M4_DIAGNOSTIC_BINARY"]).resolve(strict=True)
SHIM = pathlib.Path(os.environ["M4_SYNC_TRACE_SHIM"]).resolve(strict=True)
DICT = pathlib.Path.cwd() / ".local/native/zvec-native-profile/thirdparty/cppjieba/cppjieba-5.6.7/dict"
POLICY_PATH = pathlib.Path(__file__).resolve().parents[4] / "docs" / "benchmark-policy.json"
BENCHMARK_POLICY = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
THRESHOLDS = {
    **BENCHMARK_POLICY["resource_guards"],
    "resource_root_bytes_min": BENCHMARK_POLICY["resource_guards"]["var_tmp_bytes_min"],
}


def parse_number(value, cast):
    try:
        return cast(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"invalid host resource value: {value!r}") from exc


def host():
    mem = parse_number(next(x.split()[1] for x in pathlib.Path("/proc/meminfo").read_text().splitlines() if x.startswith("MemAvailable:")), int)
    load = parse_number(pathlib.Path("/proc/loadavg").read_text().split()[0], float)
    io_full = next(x for x in pathlib.Path("/proc/pressure/io").read_text().splitlines() if x.startswith("full "))
    io10 = parse_number(next(x.split("=", 1)[1] for x in io_full.split() if x.startswith("avg10=")), float)
    disk = parse_number(subprocess.check_output(["df", "-B1", "--output=avail", str(RESOURCE_ROOT)], text=True).splitlines()[1].strip(), int)
    mem_psi = pathlib.Path("/proc/pressure/memory").read_text()
    swap = {x.split()[0].rstrip(":"): parse_number(x.split()[1], int) for x in pathlib.Path("/proc/meminfo").read_text().splitlines() if x.startswith(("SwapTotal:", "SwapFree:"))}
    top = subprocess.check_output(["ps", "-eo", "pid,comm,%cpu,%mem,rss,args", "--sort=-%cpu"], text=True).splitlines()[:13]
    inodes = subprocess.check_output(["df", "-Pi", str(RESOURCE_ROOT)], text=True).splitlines()[-1]
    p = subprocess.run(["pgrep", "-a", "-f", "(^|/)(pacman|yay)( |$)"], text=True, capture_output=True)
    return {"time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "MemAvailable_kib": mem,
            "load1": load, "io_full_avg10": io10, "resource_root_available_bytes": disk,
            "memory_psi": mem_psi, "swap_kib": swap, "inode_report": inodes,
            "package_manager_processes": p.stdout.splitlines(), "top_processes": top}


def preflight(arm):
    h = host()
    h["thresholds"] = THRESHOLDS
    h["pass"] = (h["MemAvailable_kib"] >= THRESHOLDS["MemAvailable_kib_min"] and
                 h["load1"] <= THRESHOLDS["load1_max"] and
                 h["io_full_avg10"] <= THRESHOLDS["io_full_avg10_max"] and
                 h["resource_root_available_bytes"] >= THRESHOLDS["resource_root_bytes_min"])
    d = ARTIFACT / arm
    d.mkdir(parents=True, exist_ok=True)
    (d / "host-before.json").write_text(json.dumps(h, indent=2) + "\n")
    if not h["pass"]:
        (d / "no-launch.json").write_text(json.dumps(h, indent=2) + "\n")
        print(f"PREFLIGHT_FAIL {arm}: {h}", file=sys.stderr, flush=True)
        return False
    return True


def run(arm, enabled):
    if not preflight(arm):
        return False
    out = ARTIFACT / arm
    work = pathlib.Path(tempfile.mkdtemp(prefix="m4-sync-v2-", dir=RESOURCE_ROOT))
    if stat.S_IMODE(work.stat().st_mode) != 0o700:
        raise RuntimeError(f"unexpected private workdir mode: {work}")
    (out / "workdir.txt").write_text(str(work) + "\n")
    env = os.environ.copy()
    for key in list(env):
        if key.startswith("M4_") or key == "LD_PRELOAD":
            env.pop(key)
    env["LD_PRELOAD"] = str(SHIM)
    if enabled:
        env["M4_SYNC_TRACE_OUT"] = str(out / "sync.jsonl")
    command = [str(BIN), str(work / "root"), str(DICT), "--incremental-fts-sustained", "500", "20"]
    (out / "command.json").write_text(json.dumps({"argv": command, "trace_enabled": enabled,
        "trace_paths": False, "resource_root": str(RESOURCE_ROOT)}, indent=2) + "\n")
    trace_source = HERE / "sync_trace.c"
    sha = subprocess.check_output(["sha256sum", str(BIN), str(SHIM), str(trace_source)], text=True)
    (out / "provenance-sha256.txt").write_text(sha)
    with (out / "stdout.jsonl").open("w") as stdout, (out / "stderr.log").open("w") as stderr, (out / "pss-samples.csv").open("w") as pss:
        pss.write("monotonic_ns,pid,rss_kb,pss_kb,swap_kb\n")
        proc = subprocess.Popen(command, stdout=stdout, stderr=stderr, env=env)
        while proc.poll() is None:
            try:
                values = {}
                for line in pathlib.Path(f"/proc/{proc.pid}/smaps_rollup").read_text().splitlines():
                    key, value, *_ = line.split()
                    if key in ("Rss:", "Pss:", "Swap:"):
                        values[key] = int(value)
                pss.write(f"{time.monotonic_ns()},{proc.pid},{values.get('Rss:', 0)},{values.get('Pss:', 0)},{values.get('Swap:', 0)}\n")
                pss.flush()
            except (FileNotFoundError, ProcessLookupError):
                break
            try:
                proc.wait(timeout=0.05)
                break
            except subprocess.TimeoutExpired:
                continue
        rc = proc.wait()
    (out / "exit-code.txt").write_text(f"{rc}\n")
    (out / "host-after.json").write_text(json.dumps(host(), indent=2) + "\n")
    if rc:
        raise RuntimeError(f"{arm} exited {rc}; inspect {out}")
    if enabled:
        analyzer = HERE / "analyze_sync_trace.py"
        subprocess.run([sys.executable, str(analyzer), "--trace", str(out / "sync.jsonl"),
                        "--stdout", str(out / "stdout.jsonl"), "--out-prefix", str(out / "sync")],
                       check=True, stdout=(out / "analyzer.stdout").open("w"))
    files = sorted(p for p in out.iterdir() if p.is_file() and p.name != "SHA256SUMS.txt")
    with (out / "SHA256SUMS.txt").open("w") as f:
        subprocess.run(["sha256sum", *map(str, files)], check=True, stdout=f)
    return True


ARTIFACT.mkdir(parents=True, exist_ok=True)
if not RESOURCE_ROOT.is_dir() or not DICT.is_dir():
    raise SystemExit("resource root or Jieba dictionary is missing")
for arm, enabled in (("off", False), ("on", True)):
    if not run(arm, enabled):
        raise SystemExit(2)
print(f"AA_COMPLETE {ARTIFACT}")
