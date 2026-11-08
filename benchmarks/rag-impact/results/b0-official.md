# B0 official baseline — RAG impact

Status: **B0 MEASURED; R0 FROZEN.** Raw receipt: `b0-official.json`
(SHA-256 `e57c11e23822cf4477266b20a98d84de548ebf9b5c88e8637ddb0e912c37443c`).

This is the official baseline for the frozen targets/method in
`../PREREGISTRATION.md`. It is **not** a candidate result. The baseline is the
existing search + direct reads with evidence/relations off.

## Frozen workload

- Source: HRM2.0 `68a53a6865b81d05745c4aa47967204e8e128996`.
- Corpus: 822 manifest files / 5,400,637 bytes. Engine indexed **818** files;
  4 unsupported (3 `.csproj` + `BaseData.CS`). No source or repository index was
  written; the measurement used a private corpus copy.
- Cases: 12 real-source development cases (D01–D12); D13–D16 are synthetic and
  not part of the retrieval B0; 8 held-out cases run mechanically, not inspected
  by the implementer.
- Binary SHA-256: `83342c7db1dbf2868612c02e62d36f2afa8523f40d9a38b87e12ffaca3778786`.
- Tokenizer SHA-256: `107bbdcbad4bff1d299b7a4c3a2fb17c52890688b7dd0e4c9deab79d3c4f3d45`.

## Results

| Metric | B0 |
|---|---:|
| Dev Evidence Recall@10 (12 cases) | **0.4722** |
| Held-out Evidence Recall@10 (8 cases) | **0.3958** |
| Task-correctness proxy, dev | 0.3333 |
| Task-correctness proxy, held-out | 0.1250 |
| Additional source reads (dev mean) | 2.75 files/case |
| Additional context tokens (dev mean) | 3716 tokens/case |
| Search p50 | 22.1 ms |
| Search p95 | **63.4 ms** |
| Search p99 | 101.0 ms |
| Search max | 303.1 ms |
| Search failures / RESOURCE_BUSY | 0 / 0 |
| Concurrency 4 readers / 400 queries | 0 RESOURCE_BUSY, 0 failures |
| Idle PSS median | 558.4 MiB (571800 KiB) |
| Retrieval peak PSS | 751.1 MiB (769113 KiB) (95 samples) |
| Cold index wall (3 runs) | 0:05.31, 0:06.31, 0:07.97; median **0:06.31** |
| Cold index max RSS | 523128, 524200, 524064 KiB |

## Limitations (must travel with these numbers)

- **Task correctness is a deterministic evidence proxy**, not natural-language
  answer quality. It is the fraction of cases with all gold spans matched and no
  forbidden claim emitted. A real answer-level evaluation needs the R4/R5
  harness and owner/independent review.
- **D13–D16 are not retrieval cases.** Their fixtures are frozen and
  shape-validated; engine behavior remains an R2/R4 gate.
- **Held-out was run mechanically** by the implementer; per-case content was not
  inspected. An independent re-run is still preferable for the final claim.
- **Latency is CLI+server inclusive**, measured in server mode on one host. It is
  valid as a relative B0 for the candidate measured the same way, not as a
  universal search-latency claim.
- **One B0 run** for latency/PSS/concurrency; single-run uncertainty applies.
  Indexing uses 3 cold runs.
- The tokenizer is the local code model tokenizer; an external agent's tokenizer
  may differ.
- The corpus is the bounded pilot scope, not all of HRM2.0.

## Artifacts

- `b0-official.json` — raw receipt with per-case dev/held-out metrics.
- `b0-pilot-quality.json`, `b0-pilot-six-call-v2.json` — earlier pilots.
- `../PREREGISTRATION.md`, `../corpus-manifest.json`, `../cases/dev.json`.
