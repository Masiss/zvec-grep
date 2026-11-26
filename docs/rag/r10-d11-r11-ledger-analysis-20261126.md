# R10 / D11 / R11 ledger forensics — 2026-11-26

## Scope

Read-only analysis of existing receipts; no scheduler, formula or source changed.
C0 pack items do not store fragment IDs, so item→fragment mapping was recovered
with the P0 replay that already has ordered parity 24/24. Those IDs were joined
to the scheduler-v1 ledger by `fragment_id`. Byte attribution uses each ledger
record's `used_after - used_before`, i.e. bytes actually accepted, not merely
candidates considered.

Machine-readable detail:
`entity-scheduler-v1/ledger-forensics/R10-D11-R11-ledger-forensics.json`.

## Per-span state changes

| Case / gold span | C0 | v1 | Transition | Ledger explanation |
|---|---|---|---|---|
| R10 `arg_matches.rs` 4047–4195 | exact | exact | preserved | Rank 1, entity `0000002f609…`, first in both; 1,817 bytes accepted. |
| R10 `arg_matches.rs` 39860–40369 | exact | absent | lost | C0 rank 12 reached v1 at position 29, but only 428 bytes remained vs 1,786 needed. |
| R10 `arg_matches.rs` 45429–45798 | exact | absent | lost | C0 rank 28 reached v1 at position 33, but only 428 bytes remained vs 1,765 needed after clipping. |
| D11 `EmployeeCommand.cs` 35152–35841 | exact | partial 572/689 | lost | C0 rank 1 reached v1 at position 139, but only 97 bytes remained vs 117 still-unique bytes needed. |
| R11 `validator.rs` 624–2114 | partial 127/1490 | exact | rescued | Same entity supplied missing prefix via rank 66; no foreign entity bytes preceded it. |
| R11 `validator.rs` 2092–3453 | exact | exact | preserved | v1 rank 10 from the prioritized entity covered the whole span; C0's second entity became redundant. |
| R11 `validator.rs` 7721–9962 | exact | exact | preserved | v1 ranks 13/7/11 from the prioritized entity covered the span; C0's rank 2 from another entity became duplicate. |

## R10 — one broad entity consumed the budget

All three entities are in the same file, `clap_builder/src/parser/matches/arg_matches.rs`:

| Entity | Entity range | Role |
|---|---|---|
| `0000002f60987cf8d756c212d0a4352f` | 2621–31973 | Rank 1; preserved first span; consumed 24,148 bytes in v1 |
| `0000002fc35cea95e43d5cd7b7135a3b` | 39781–45201 | Rank 12; C0 delivered second span |
| `0000002f3fc094ffe3b1be4588a21f5c` | 45222–48603 | Rank 28; C0 delivered third span |

| C0 fragment | C0 candidate state | v1 candidate state | Prior byte consumers in v1 |
|---|---|---|---|
| Rank 12, entity `0000002fc35c…` | used 10,588 → 12,374; accepted 1,786 | position 29; used_before 24,148; remaining 428; cost 1,786; accepted 0; `original_not_fit` | Entity `0000002f609…`: **24,148** |
| Rank 28, entity `0000002f3fc0…` | used 20,594 → 21,993; accepted 1,399 in two pieces | position 33; used_before 24,148; remaining 428; clipped cost 1,765; accepted 0; `original_not_fit` | Entity `0000002f609…`: **24,148** |

The target fragments were not skipped by the scheduler and did not fail
resolution. They arrived after the budget was effectively gone. The resolver
was never called for them because the original fragment did not fit.

This is **intra-file entity starvation**: a broad high-priority entity in the
same file crowded out two smaller gold-bearing entities.

## D11 — three other entities consumed the budget; wrong entity left partial bytes

Target C0 fragment:

- Rank **1**, entity `0000013c2a15b617fdfe3a1628698400`, `EmployeeCommand.cs`
  entity range **35152–35841**.
- C0: used 0 → 689; accepted the full 689-byte gold span.
- v1: processing position **139**; `used_before = 24,479`; remaining **97**;
  clipped original cost **117**; accepted **0**; `original_not_fit`.
- Shortfall: **20 bytes**. Resolver was never called.

Entities that actually consumed bytes before it:

| Entity | Path | Accepted bytes before target |
|---|---|---:|
| `0000013c90b928a90cd136e1e420ab33` | `EmployeeCommand.cs`, range 362–40110 | **23,087** |
| `0000015b0c7701740be0555779676123` | `WorkLogResource.cs`, range 123–35049 | **1,284** |
| `0000013c01cd75b4db2d55702ea26122` | `EmployeeCommand.cs`, range 8337–20132 | **108** |
| **Total** | | **24,479** |

The partial v1 coverage came from a different entity:

- Rank **48**, entity `0000013c90b…`, processed first with `used_before = 0`.
- C0: rank 48 was `not_fit` at used 24,358 with 1,249 bytes requested.
- v1: accepted **1,821** bytes, range **33903–35724**.
- That wrong-entity range covers **572/689** gold bytes, leaving only
  **35724–35841** (117 bytes) for the true rank-1 fragment. The remaining
  budget was 97, so the true fragment missed by 20 bytes.

This is **foreign-entity budget starvation plus partial overlap from the wrong
entity**, not a declaration-boundary failure.

## R11 — correct entity coherence rescued the span

Gold-bearing entity `00000035a5afa6300fab0c4af6a005b0` covers
`validator.rs` **451–15737**.

For the rescued span 624–2114:

| Fragment | C0 state | v1 state | Prior accepted bytes in v1 |
|---|---|---|---|
| Rank 10, `…0001` | used 10,031 → 10,836; accepted 805, piece 1987–2792; only 127/1490 gold bytes | position 4; used 5,087 → 6,905; accepted 1,818, piece 1987–3805 | **5,087**, all from the same entity |
| Rank 66, `…0000` | used 24,509; remaining 67; requested 1,536; `not_fit` | position 8; used 11,209 → 12,745; accepted 1,536, piece 451–1987 | **11,209**, all from the same entity |

Rank 66 supplied the missing 624–1987 prefix. It moved from C0 `not_fit`
(67 bytes remaining) to v1 accepted with 13,367 bytes remaining. No foreign
entity consumed a byte before either required fragment.

For the other two R11 spans, C0 depended on fragments from multiple entities
(rank 6 and rank 2). In v1 those foreign fragments were duplicate or arrived
after the budget filled, but the prioritized entity's own ranks 10 and 13/7/11
already covered the spans. The case stayed exact without them.

This is **entity-coherent coverage**, not foreign-entity displacement and not a
new resolver boundary.

## Separated causes

1. **R10 and D11 losses: budget starvation.** The true C0 fragments reached the
   v1 ledger but were rejected as `original_not_fit`; prior entities had
   consumed 24,148 and 24,479 bytes. No resolver boundary was attempted.
2. **R11 rescue: same-entity coverage.** The needed late-in-C0 rank 66 fragment
   was accepted early enough in v1, and every preceding byte belonged to the
   same gold-bearing entity. Foreign C0 fragments became redundant.

The evidence does not support blaming these three state changes on declaration
selection, coordinate repair, or pack-byte union. It isolates entity ordering
and budget occupancy as the causal mechanism.
