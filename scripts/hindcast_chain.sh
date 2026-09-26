#!/bin/zsh
# 과거 사이클 체인: 수집 → 24h 파랑 예보(JAX) → 콜로케이션  (표본 축적용)
cd "$(dirname "$0")/.."
for C in "$@"; do
  echo "=== $C ingest";  .venv/bin/python -m poseidon.scheduler.ingest_cycle --once --steps 24 --cycle $C 2>&1 | grep -E "GRID_OK|FAIL|done" | tail -3
  echo "=== $C wave";    .venv/bin/python -m poseidon.scheduler.wave_cycle --cycle $C --hours 24 2>&1 | grep -E "final lead" | tail -1
  echo "=== $C colloc";  .venv/bin/python -m poseidon.validation.collocate --cycle $C 2>&1 | grep -E "collocated" | tail -1
done
echo "=== CHAIN DONE"
