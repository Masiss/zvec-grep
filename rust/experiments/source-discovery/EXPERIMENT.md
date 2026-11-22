# Offline source discovery experiment

This directory freezes the synthetic corpus and 12 tasks before candidate
implementation. Expected entry points live in `fixtures/cases.json`. Baseline
outputs must be captured with ordinary `rg -n -F -- <query> fixtures/corpus`
before the candidate code is written. Keep development fixtures separate from
any later independent validation set.

The intended prototype is opt-in and offline. It must not touch the persisted
workspace index, model artifacts, production defaults, or public APIs. Any
result describes this synthetic fixture only, not agent behavior on real
repositories.
