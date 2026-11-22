# Offline source discovery experiment

This directory freezes the synthetic corpus and 12 tasks before candidate
implementation. Expected entry points live in `fixtures/cases.json`. Baseline
The task set and expected entry points were frozen before candidate code was
written. The first ordinary-`rg` baseline attempt used a doubled relative path
and is preserved in `baseline-rg.json` as an invalid run. `evaluate.py` reruns
ordinary `rg -n -F -- <query>
rust/experiments/source-discovery/fixtures/corpus` from the repository root
alongside the candidate against the unchanged frozen inputs. That correction
was made after initial prototype results, so the output comparison is
reproducible but baseline timing was not preregistered. Keep development
fixtures separate from the independent validation set.

The intended prototype is opt-in and offline. It must not touch the persisted
workspace index, model artifacts, production defaults, or public APIs. Any
result describes this synthetic fixture only, not agent behavior on real
repositories.
