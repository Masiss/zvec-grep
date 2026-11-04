#!/usr/bin/env python3
"""One resource-gated 500x20 pair for reconstructed pre-change vs candidate EvalFilter."""
import json
import os
import pathlib
import stat
import subprocess
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent
PAIR = HERE / "matched-evaluator-pair-20261019"
RESOURCE_ROOT = pathlib.Path(os.environ.get("M4_RESOURCE_ROOT", tempfile.gettempdir())).resolve(strict=True)
BUILD_ROOT = pathlib.Path(os.environ["M4_MATCHED_BUILD_ROOT"]).resolve(strict=True)
DICT = pathlib.Path.cwd() / ".local/native/zvec-native-profile/thirdparty/cppjieba/cppjieba-5.6.7/dict"
POLICY_PATH = pathlib.Path(__file__).resolve().parents[4] / "docs" / "benchmark-policy.json"
BENCHMARK_POLICY = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
THRESHOLDS = BENCHMARK_POLICY["resource_guards"]
ARMS = [
    ("prechange-evalfilter", BUILD_ROOT / "baseline/build/m4_bridge_integration"),
    ("candidate-evalfilter", BUILD_ROOT / "candidate/build/m4_bridge_integration"),
]


def read_host():
    mem = int(next(line.split()[1] for line in pathlib.Path("/proc/meminfo").read_text().splitlines() if line.startswith("MemAvailable:")))
    load = float(pathlib.Path("/proc/loadavg").read_text().split()[0])
    io_full = next(line for line in pathlib.Path("/proc/pressure/io").read_text().splitlines() if line.startswith("full "))
    io10 = float(next(x.split("=", 1)[1] for x in io_full.split() if x.startswith("avg10=")))
    disk = int(subprocess.check_output(["df", "-B1", "--output=avail", str(RESOURCE_ROOT)], text=True).splitlines()[1].strip())
    mem_psi = pathlib.Path("/proc/pressure/memory").read_text()
    swap = {line.split()[0].rstrip(":"): int(line.split()[1]) for line in pathlib.Path("/proc/meminfo").read_text().splitlines() if line.startswith(("SwapTotal:", "SwapFree:"))}
    top = subprocess.check_output(["ps", "-eo", "pid,comm,%cpu,%mem,rss,args", "--sort=-%cpu"], text=True).splitlines()[:13]
    inodes = subprocess.check_output(["df", "-Pi", str(RESOURCE_ROOT)], text=True).splitlines()[-1]
    pkg_result = subprocess.run(["pgrep", "-a", "-f", "(^|/)(pacman|yay)( |$)"], text=True, capture_output=True)
    pkg = pkg_result.stdout.splitlines()
    return {"time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "MemAvailable_kib": mem,
            "load1": load, "io_full_avg10": io10, "resource_root_available_bytes": disk,
            "memory_psi": mem_psi, "swap_kib": swap, "var_tmp_inode_report": inodes,
            "package_manager_processes": pkg, "top_processes": top}


def preflight(arm):
    s = read_host()
    s["thresholds"] = THRESHOLDS
    s["pass"] = (s["MemAvailable_kib"] >= THRESHOLDS["MemAvailable_kib_min"] and
                 s["load1"] <= THRESHOLDS["load1_max"] and
                 s["io_full_avg10"] <= THRESHOLDS["io_full_avg10_max"] and
                 s["resource_root_available_bytes"] >= THRESHOLDS["var_tmp_bytes_min"])
    (PAIR / f"{arm}.host-before.json").write_text(json.dumps(s, indent=2) + "\n")
    if not s["pass"]:
        (PAIR / f"{arm}.no-launch.json").write_text(json.dumps(s, indent=2) + "\n")
        print(f"PREFLIGHT_FAIL {arm}: {s}", file=sys.stderr, flush=True)
        return False
    return True


def sample_pss(proc, path):
    with path.open("w") as out:
        out.write("monotonic_ns,pid,rss_kb,pss_kb,swap_kb\n")
        while proc.poll() is None:
            try:
                fields = {}
                for line in pathlib.Path(f"/proc/{proc.pid}/smaps_rollup").read_text().splitlines():
                    key, value, *_ = line.split()
                    if key in ("Rss:", "Pss:", "Swap:"):
                        fields[key] = int(value)
                out.write(f"{time.monotonic_ns()},{proc.pid},{fields.get('Rss:', 0)},{fields.get('Pss:', 0)},{fields.get('Swap:', 0)}\n")
                out.flush()
            except (FileNotFoundError, ProcessLookupError):
                break
            try:
                proc.wait(timeout=0.05)
                break
            except subprocess.TimeoutExpired:
                continue


def run_arm(name, binary):
    if not preflight(name):
        return False
    secure_workdir = pathlib.Path(tempfile.mkdtemp(prefix="m4-writer-tail-", dir=RESOURCE_ROOT))
    if stat.S_IMODE(secure_workdir.stat().st_mode) != 0o700:
        raise RuntimeError(f"unexpected workload directory mode: {secure_workdir}")
    workroot = secure_workdir / "root"
    outdir = PAIR / name
    outdir.mkdir(parents=True, exist_ok=True)
    command = [str(binary), str(workroot), str(DICT), "--incremental-fts-sustained", "500", "20"]
    (outdir / "command.json").write_text(json.dumps({"argv": command, "cwd": str(pathlib.Path.cwd()), "env_overrides": {}, "resource_root": str(RESOURCE_ROOT), "private_workdir": str(secure_workdir), "work_root": str(workroot)}, indent=2) + "\n")
    env = os.environ.copy()
    for key in list(env):
        if key.startswith("M4_") or key == "LD_PRELOAD":
            env.pop(key)
    with (outdir / "stdout.jsonl").open("w") as stdout, (outdir / "stderr.log").open("w") as stderr:
        proc = subprocess.Popen(command, stdout=stdout, stderr=stderr, env=env)
        sample_pss(proc, outdir / "pss-samples.csv")
        rc = proc.wait()
    (outdir / "exit-code.txt").write_text(f"{rc}\n")
    after = read_host()
    (outdir / "host-after.json").write_text(json.dumps(after, indent=2) + "\n")
    if rc != 0:
        raise RuntimeError(f"{name} exited {rc}; see {outdir}")
    return True


PAIR.mkdir(parents=True, exist_ok=True)
if not RESOURCE_ROOT.is_dir() or not BUILD_ROOT.is_dir():
    raise SystemExit("resource/build root is missing or not a directory")
if not DICT.is_dir():
    raise SystemExit(f"missing Jieba dictionary: {DICT}")
for name, binary in ARMS:
    if not binary.is_file():
        raise SystemExit(f"missing matched binary: {binary}")
    if not run_arm(name, binary):
        raise SystemExit(2)
print(f"PAIR_COMPLETE {PAIR}")
