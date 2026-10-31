# Release manifest — zvec-grep 0.3.0-rc.2

Use this manifest as the baseline for Optimization V2. It is a release-candidate
baseline, not a production-qualification record.

| Field | Value |
|---|---|
| Release | zvec-grep `0.3.0-rc.2` (release candidate) |
| Branch | `integration/upstream-rust` |
| Upstream base | `e1f93d257800f8626eb877eaf45573518076ab65` |
| **Release code commit** | `289674bc127f664fea0993381c6d109302af95ae` |
| Release tag | `rust-v0.3.0-rc.2` (annotated/lightweight local tag at the final docs HEAD; not pushed) |
| Documentation bundle | `docs/release/` in the tagged HEAD |
| RC status | **READY for merge/release review** |
| Production status | **NOT READY** — no 421-file/production benchmark; M3-B native blocker; #10 not implemented |
| M3-B | blocked by zvec 0.8.0; outside-lock path not shipped; not an RC blocker |
| #10 | deferred maintenance not implemented; not an RC blocker |

The release-code commit is the stable code baseline. The release tag additionally
includes the release notes, manifest, PR description, gates, evidence, and
rollback instructions; docs-only changes after the code commit do not alter the
release binary.

## Verification baseline

| Check | Result |
|---|---|
| Engine lib | 702 passed, 0 failed, 15 ignored |
| Engine integration | 40 passed |
| Daemon / CLI / MCP / protocol / FP16 / host | 124 / 180 / 60 / 5 / 2 / 44 |
| **Total** | **1,157 passed, 0 failed, 15 ignored** |
| Clippy (`-D warnings`) | PASS |
| `cargo fmt --check` | PASS |
| Release build (`zg-cli`) | PASS |
| CLI/MCP smoke | PASS: agent 1 tool, full 6 tools, protocol `2024-11-05` |

The 15 ignored tests are 5 handle/native-maintenance gates (zvec 0.8.0 blocker)
and 10 local-model/benchmark/oracle tests requiring external models or manual
execution.

## Artifacts

| Artifact | SHA-256 / location |
|---|---|
| Release binary `zg` | `ad1d9c2c1aca53d1b1c113ca84f5b1006289592df9e8e85b9f18d1629fba7d0a` |
| `mcp-agent.out` | `16840ba4adcf5b8cd87d5c1d099a525d1585490fb7b250b4ce290c63541bab6e` |
| `mcp-full.out` | `385a5248b7269a1a142cf4cd3e1adc340d988df91738f836ecb7be48dce7c38e` |
| `summary.json` | `9f471b0cc6280d8b84c6a575f110196495e0e811a8c0a998244f4675e6f31af1` |
| Release smoke bundle | `/home/masis/bench/release-readiness-20261031/` |
| 64-file confirmation | `/home/masis/bench/m3b-confirm/results-20261031T100004Z/` |
| Native-gate blocker evidence | `/home/masis/bench/m3b-native-gate/run.log` |

## Baseline map

- Release notes: `RELEASE-NOTES-0.3.0-rc.2.md`
- Release gates / rollback: `RELEASE-READINESS.md`
- Merge package: `MERGE-READINESS.md`
- Feature dispositions: `FEATURE-MATRIX.md`
- Benchmark plan/results: `BENCHMARK-PLAN.md`, `BENCHMARK-RESULTS.md`
- M3-B feasibility/blocker: `M3B-HANDLE-MAINTENANCE-FEASIBILITY.md`
- Current/historical handoffs: `../../HANDOFF-20261031-NEXT.md`, `../../HANDOFF.md`
- PR description: `PR-DESCRIPTION.md`
