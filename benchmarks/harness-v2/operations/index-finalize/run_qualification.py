#!/usr/bin/env python3
"""Non-blocking, resumable Index/Finalize Qualification V1 runner.

Outer mode schedules one systemd scope per cell. Inner mode starts an isolated
daemon, warms the workspaces once, then runs the three measured repetitions and
writes one receipt per repetition. Existing receipts are skipped on resume.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback

ROOT = pathlib.Path(__file__).resolve().parents[4]
BIN = ROOT / "rust/target/release/zg"
OPS = ROOT / "benchmarks/harness-v2/operations/index-finalize"
WORK = ROOT / ".tmp/ifq"
BASE = WORK / "base"
CELLS = WORK / "cells"
RESULTS = OPS / "results/runs"
PROTOCOL = OPS / "qualification-protocol.json"
MANIFEST = OPS / "fixture-manifest.v1.json"
CELL_TYPES = ["dirty_insert", "dirty_delete", "clean", "restart_insert", "restart_delete"]
WS_COUNTS = [1, 4, 8]
REPS = 3
SEED = 20261101

def sha(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def run(cmd, *, env=None, cwd=None, timeout=1800):
    return subprocess.run(cmd, cwd=cwd, env=env, text=True, capture_output=True, timeout=timeout)

def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port

def base_env(state: pathlib.Path):
    env = os.environ.copy()
    env["HOME"] = str(state / "home")
    env["USERPROFILE"] = str(state / "home")
    env["ZVEC_GREP_HOME"] = str(state / "runtime")
    env.pop("ZVEC_GREP_SERVER_TOKEN", None)
    env.pop("ZVEC_GREP_SERVER_TOKEN_FILE", None)
    return env

def cgroup_dir():
    rel = pathlib.Path("/proc/self/cgroup").read_text().strip().split(":", 2)[2]
    return pathlib.Path("/sys/fs/cgroup") / rel.lstrip("/")

def read_int(path):
    try:
        return int(path.read_text().strip())
    except Exception:
        return None

def pss_rss(pid):
    p = pathlib.Path(f"/proc/{pid}/smaps_rollup")
    vals = {}
    try:
        for line in p.read_text().splitlines():
            if line.startswith("Pss:"):
                vals["pss_kib"] = int(line.split()[1])
            elif line.startswith("Rss:"):
                vals["rss_kib"] = int(line.split()[1])
            elif line.startswith("SwapPss:"):
                vals["swap_pss_kib"] = int(line.split()[1])
    except Exception:
        return None
    return vals

def cgroup_sample(cg) -> dict[str, object]:
    sample: dict[str, object] = {"t": time.time()}
    for name in ["memory.current", "memory.peak", "memory.swap.current", "memory.swap.peak", "pids.current"]:
        v = read_int(cg / name)
        if v is not None:
            sample[name.replace(".", "_")] = v
    for name in ["memory.pressure", "io.pressure", "cpu.pressure"]:
        try:
            sample[name.replace(".", "_")] = (cg / name).read_text().strip()
        except Exception:
            pass
    try:
        procs = [int(x) for x in (cg / "cgroup.procs").read_text().split()]
    except Exception:
        procs = []
    sample["cgroup_procs"] = procs
    processes: list[dict[str, int]] = []
    for pid in procs:
        v = pss_rss(pid)
        if v:
            processes.append({"pid": pid, **v})
    sample["processes"] = processes
    sample["process_pss_kib"] = sum(p.get("pss_kib", 0) for p in processes)
    sample["process_rss_kib"] = sum(p.get("rss_kib", 0) for p in processes)
    return sample

class Sampler(threading.Thread):
    def __init__(self, cg, path):
        super().__init__(daemon=True)
        self.cg, self.path, self.stop = cg, path, threading.Event()
        self.samples = []
    def run(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w") as f:
            while not self.stop.is_set():
                s = cgroup_sample(self.cg)
                f.write(json.dumps(s, sort_keys=True) + "\n")
                f.flush()
                self.samples.append(s)
                time.sleep(0.25)
    def finish(self):
        self.stop.set()
        self.join(timeout=5)
        peaks = {"process_pss_kib": 0, "process_rss_kib": 0, "memory_peak": 0, "swap_current": 0}
        for s in self.samples:
            peaks["process_pss_kib"] = max(peaks["process_pss_kib"], s.get("process_pss_kib", 0))
            peaks["process_rss_kib"] = max(peaks["process_rss_kib"], s.get("process_rss_kib", 0))
            peaks["memory_peak"] = max(peaks["memory_peak"], s.get("memory_peak", 0) or 0)
            peaks["swap_current"] = max(peaks["swap_current"], s.get("memory_swap_current", 0) or 0)
        return peaks

def env_for(state):
    e = base_env(state)
    e["ZVEC_GREP_MODE"] = "server"
    return e

def start_daemon(state, env, listen):
    r = run([str(BIN), "--server", "on", "--listen", listen], env=env, cwd=state)
    if r.returncode != 0:
        raise RuntimeError(f"daemon start failed: {r.stderr}")
    for _ in range(100):
        s = run([str(BIN), "--server", "status", "--check-ready"], env=env, cwd=state)
        if s.returncode == 0:
            return
        time.sleep(0.1)
    raise RuntimeError("daemon did not become ready")

def stop_daemon(state, env):
    run([str(BIN), "--server", "off"], env=env, cwd=state, timeout=60)

def warm(roots, state, env):
    for root in roots:
        r = run([str(BIN), "--index", "--mode", "server", "--no-color", str(root)], env=env, cwd=state, timeout=1800)
        if r.returncode != 0:
            raise RuntimeError(f"warm index failed for {root}: {r.stderr[-4000:]}")

def reset_sources(base, root):
    src = root / "src"
    if src.exists():
        try:
            shutil.rmtree(src)
        except OSError as error:
            raise RuntimeError(f"failed to reset {src}: {error}") from error
    shutil.copytree(base / "src", src)

def churn(kind, root, rep):
    src = root / "src"
    allfiles = sorted(src.rglob("*.rs"))
    if kind == "clean":
        return []
    inserted = []
    if kind == "dirty_insert":
        for i in range(200):
            p = src / f"insert_rep{rep}_{i:04d}.rs"
            p.write_text(f"pub fn inserted_{rep}_{i:04d}() {{}}\npub const T_{rep}_{i:04d}: &str = \"inserted_{rep}_{i:04d}\";\n")
            inserted.append(p)
        for p in allfiles[:100]:
            p.write_text(p.read_text() + f"\n// modified rep {rep}\npub const M_{rep}_{p.stem}: usize = {rep};\n")
    elif kind == "dirty_delete":
        for i, p in enumerate(allfiles):
            if i % 10 == 0:
                p.unlink()
            elif i < 200:
                p.write_text(p.read_text() + f"\n// modified rep {rep}\n")
    return inserted

def jobs_json(state, env):
    r = run([str(BIN), "--server", "jobs", "--all", "--json"], env=env, cwd=state)
    if r.returncode != 0:
        return []
    try:
        return json.loads(r.stdout)
    except Exception:
        return []

def wait_jobs(roots, state, env, timeout=1800):
    canon = {str(r.resolve()): r for r in roots}
    start = time.time()
    queue_samples = []
    seen_running = {k: 0 for k in canon}
    while time.time() - start < timeout:
        jobs = jobs_json(state, env)
        active = [j for j in jobs if str(pathlib.Path(j["root"])) in canon and j["state"] in ("queued", "running")]
        running = [j for j in active if j["state"] == "running"]
        for j in running:
            root = str(pathlib.Path(j["root"]))
            seen_running[root] = max(seen_running[root], 1)
            if len([x for x in running if str(pathlib.Path(x["root"])) == root]) > 1:
                raise RuntimeError(f"single-writer violation for {root}")
        queue_samples.append({"t": time.time(), "active": len(active), "running": len(running), "queued": len(active)-len(running), "jobs": [{k: j.get(k) for k in ("id","root","reason","state","queue_position","waited_millis")} for j in active]})
        if not active:
            return queue_samples, jobs
        time.sleep(0.5)
    raise RuntimeError("timed out waiting for index jobs")

def fts(term, root, env, route="--fts"):
    r = run([str(BIN), route, term, "--mode", "server", "--no-color"], env=env, cwd=root, timeout=300)
    return r.returncode, r.stdout, r.stderr

def correctness(kind, rep, roots, state, env, inserted):
    checks = []
    for idx, root in enumerate(roots):
        term = "finalize_0000"
        rc, out, err = fts(term, root, env)
        checks.append({"check": f"fts_present_{idx}", "ok": rc == 0 and "file_0000.rs" in out, "tail": (out+err)[-500:]})
        rc, out, err = fts("inserted_%d_0000" % rep, root, env) if kind == "dirty_insert" else (0, "", "")
        if kind == "dirty_insert":
            checks.append({"check": f"fts_inserted_{idx}", "ok": rc == 0 and "insert_rep" in out, "tail": (out+err)[-500:]})
        if kind == "dirty_delete":
            rc, out, err = fts("finalize_0000", root, env)
            checks.append({"check": f"fts_deleted_{idx}", "ok": "file_0000.rs" not in out, "tail": (out+err)[-500:]})
    return checks

def do_rep(cell, rep, roots, state, env, cg, cell_dir):
    out = RESULTS / cell / f"rep-{rep}.json"
    if out.exists():
        try:
            existing = json.loads(out.read_text())
            if existing.get("status") == "pass":
                return existing
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(f"failed to read existing receipt {out}: {error}") from error
    rep_dir = cell_dir / f"rep-{rep}"
    raw = rep_dir / "telemetry.jsonl"
    # fresh source tree, preserved warm index
    for root in roots:
        reset_sources(BASE / root.name, root)
    inserted = []
    for root in roots:
        inserted.extend(churn(cell, root, rep))
    (cg / "memory.peak").write_text("0\n")
    sampler = Sampler(cg, raw)
    sampler.start()
    phase = {"start": time.time()}
    try:
        submit = []
        for root in roots:
            r = run([str(BIN), "--index", "--mode", "server", "--no-wait", "--no-color", str(root)], env=env, cwd=state, timeout=120)
            if r.returncode != 0:
                raise RuntimeError(f"submit failed for {root}: {r.stderr[-2000:]}")
            submit.append(r.stdout.strip()[-200:])
        phase["submitted"] = time.time()
        queue_samples, jobs = wait_jobs(roots, state, env)
        phase["jobs_done"] = time.time()
        failures = [
            j for j in jobs
            if any(str(pathlib.Path(j["root"])) == str(r.resolve()) for r in roots)
            and (j["state"] == "failed" or (j["state"] == "cancelled" and j.get("reason") != "watch"))
        ]
        if failures:
            raise RuntimeError(f"job failures: {failures}")
        checks = correctness(cell, rep, roots, state, env, inserted)
        if not all(c["ok"] for c in checks):
            raise RuntimeError(f"correctness failed: {checks}")
        phase["correctness"] = time.time()
        if cell.startswith("restart"):
            stop_daemon(state, env)
            start_daemon(state, env, "127.0.0.1:%d" % free_port())
            rc, out, err = fts("finalize_0001", roots[0], env)
            if rc != 0 or "file_0001.rs" not in out:
                raise RuntimeError("reopen retrieval parity failed")
            phase["restart_ready"] = time.time()
        peaks = sampler.finish()
        receipt = {
            "cell": cell, "rep": rep, "roots": [str(r) for r in roots],
            "status": "pass", "phases": phase, "peaks": peaks,
            "queue_samples": queue_samples,
            "correctness": checks,
            "storage_bytes": {str(r): sum(p.stat().st_size for p in (r/'.zvec-grep').rglob('*') if p.is_file()) for r in roots},
            "submit_tails": submit,
        }
    except Exception as error:
        try:
            peaks = sampler.finish()
        except Exception:
            peaks = {}
        receipt = {"cell": cell, "rep": rep, "status": "fail", "error": str(error), "trace": traceback.format_exc(), "phases": phase, "peaks": peaks}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    return receipt

def inside(cell, ws, state, listen):
    cg = cgroup_dir()
    cell_dir = CELLS / cell
    cell_dir.mkdir(parents=True, exist_ok=True)
    roots = [cell_dir / f"ws_{i}" for i in range(1, ws + 1)]
    env = env_for(state)
    stop_daemon(state, env)
    start_daemon(state, env, listen)
    try:
        if not (cell_dir / "warm.done").exists():
            warm(roots, state, env)
            (cell_dir / "warm.done").write_text("ok\n")
        for rep in range(1, REPS + 1):
            r = do_rep(cell, rep, roots, state, env, cg, cell_dir)
            print(json.dumps({"cell": cell, "rep": rep, "status": r["status"]}))
            if r["status"] != "pass":
                return 1
    finally:
        stop_daemon(state, env)
    return 0

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inside-cell", choices=[f"{w}_{c}" for w in WS_COUNTS for c in CELL_TYPES])
    ap.add_argument("--inside-ws", type=int)
    ap.add_argument("--inside-state", type=pathlib.Path)
    ap.add_argument("--listen")
    ap.add_argument("--preflight", action="store_true")
    a = ap.parse_args()
    if a.inside_cell:
        return inside(a.inside_cell, a.inside_ws, a.inside_state, a.listen)
    if a.preflight:
        checks = {"binary": sha(BIN), "protocol": sha(PROTOCOL), "manifest": sha(MANIFEST),
                  "smaps": pathlib.Path("/proc/self/smaps_rollup").exists(),
                  "model": pathlib.Path("/home/masis/casehome/models/model2vec/minishlab--potion-code-16M-v2/e9d2a44ca6a05ac6685f3b23709ea57eb7352d5b/model.safetensors").exists(),
                  "free_bytes": shutil.disk_usage(ROOT).free}
        print(json.dumps(checks, indent=2))
        return 0 if all([checks["smaps"], checks["model"], checks["free_bytes"] > 10*2**30]) else 1
    # outer
    if not BASE.exists():
        subprocess.run([sys.executable, str(OPS/"gen_fixture.py"), "--out", str(BASE), "--manifest", str(MANIFEST), "--copies", "8"], check=True)
    RESULTS.mkdir(parents=True, exist_ok=True)
    for ws in WS_COUNTS:
        for cell in CELL_TYPES:
            cid = f"{ws}_{cell}"
            done = RESULTS / cid / "cell.done"
            if done.exists():
                continue
            state = CELLS / cid / "state"
            state.mkdir(parents=True, exist_ok=True)
            for i in range(1, ws + 1):
                dst = CELLS / cid / f"ws_{i}"
                if not (CELLS / cid / "warm.done").exists():
                    if dst.exists():
                        try:
                            shutil.rmtree(dst)
                        except OSError as error:
                            raise RuntimeError(f"failed to remove {dst}: {error}") from error
                    shutil.copytree(BASE / f"ws_{i}", dst)
            listen = f"127.0.0.1:{free_port()}"
            cmd = ["systemd-run", "--user", "--scope", "--collect", "-p", "MemoryAccounting=yes", f"--unit=zg-ifq-{cid}-{int(time.time())}", sys.executable, str(pathlib.Path(__file__)), "--inside-cell", cid, "--inside-ws", str(ws), "--inside-state", str(state), "--listen", listen]
            r = subprocess.run(cmd, text=True, capture_output=True)
            (RESULTS / cid).mkdir(parents=True, exist_ok=True)
            (RESULTS / cid / "outer.log").write_text(r.stdout + r.stderr)
            if r.returncode == 0:
                done.write_text("ok\n")
            else:
                print(f"cell {cid} failed rc={r.returncode}")
                return r.returncode
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
