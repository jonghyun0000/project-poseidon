#!/bin/zsh
# 72h 리드 사이클: 수집(0-72h) → L1 72h 예보 → 콜로케이션
cd "$(dirname "$0")/.."
for C in "$@"; do
  echo "=== $C ingest 72h"
  .venv/bin/python -m poseidon.scheduler.ingest_cycle --once --steps 72 --cycle $C 2>&1 | grep -E "GRID_OK|GRID_FAIL|done" | tail -2
  echo "=== $C wave 72h"
  .venv/bin/python -m poseidon.scheduler.wave_cycle --cycle $C --hours 72 2>&1 | grep -E "grid, dt|final lead|Traceback" | tail -2
  echo "=== $C colloc"
  .venv/bin/python -m poseidon.validation.collocate --cycle $C 2>&1 | grep -E "collocated|no colloc" | tail -1
done
echo "=== CYCLE72 DONE"
