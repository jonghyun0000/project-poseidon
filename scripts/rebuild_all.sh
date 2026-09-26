#!/bin/zsh
# B-1: 동결된 엔진 설정(uno2/none)으로 전 사이클 재생산 + 재콜로케이션
# 재사용하는 zarr 없음. 각 산출물은 attrs에 자기 출처를 기록한다.
cd "$(dirname "$0")/.."
L2_CYCLES="20260819T00 20260820T00 20260821T00"
for C in "$@"; do
  echo "=== $C L1"
  .venv/bin/python -m poseidon.scheduler.wave_cycle --cycle $C --hours 24 2>&1 | grep -E "final lead|Traceback" | tail -1
  if [[ " $L2_CYCLES " == *" $C "* ]]; then
    echo "=== $C L2"
    .venv/bin/python -m poseidon.scheduler.nested_cycle --cycle $C --hours 24 2>&1 | grep -E "L2 done|Traceback" | tail -1
    .venv/bin/python -m poseidon.validation.collocate --cycle $C --level L2 2>&1 | grep -E "collocated" | tail -1
  fi
  echo "=== $C colloc L1"
  .venv/bin/python -m poseidon.validation.collocate --cycle $C 2>&1 | grep -E "collocated|no colloc" | tail -1
done
echo "=== REBUILD DONE"
