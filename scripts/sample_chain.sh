#!/bin/zsh
# 표본 축적 체인: 수집 → L1 24h 예보 → 콜로케이션 (KMA 관측 백필 구간과 겹치는 사이클)
cd "$(dirname "$0")/.."
for C in "$@"; do
  echo "=== $C ingest"
  .venv/bin/python -m poseidon.scheduler.ingest_cycle --once --steps 24 --cycle $C 2>&1 | grep -E "GRID_OK|GRID_FAIL|done" | tail -2
  echo "=== $C wave"
  .venv/bin/python -m poseidon.scheduler.wave_cycle --cycle $C --hours 24 2>&1 | grep -E "final lead|Traceback" | tail -1
  echo "=== $C colloc"
  .venv/bin/python -m poseidon.validation.collocate --cycle $C 2>&1 | grep -E "collocated|no colloc" | tail -1
done
echo "=== SAMPLE CHAIN DONE"
