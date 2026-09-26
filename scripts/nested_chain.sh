#!/bin/zsh
cd "$(dirname "$0")/.."
for C in "$@"; do
  echo "=== $C nested"; .venv/bin/python -m poseidon.scheduler.nested_cycle --cycle $C --hours 24 2>&1 | grep -E "L2 done|Traceback" | tail -1
  echo "=== $C colloc L2"; .venv/bin/python -m poseidon.validation.collocate --cycle $C --level L2 2>&1 | grep collocated
done
echo "=== NESTED CHAIN DONE"
