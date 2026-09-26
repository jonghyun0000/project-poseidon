"""Content-addressed route candidates with canonical inputs and immutable provenance."""
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from poseidon.core.config import settings
from poseidon.routing.engine import RoutingError


def atomic_bytes(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as file:
        file.write(data)
        file.flush()
        os.fsync(file.fileno())
        name = Path(file.name)
    try:
        os.replace(name, path)
    finally:
        name.unlink(missing_ok=True)


def encode(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def plans_root():
    return settings.data_root / "routing/plans"


def save_plan(plan):
    data = encode(plan)
    digest = hashlib.sha256(data).hexdigest()
    atomic_bytes(plans_root() / f"{digest}.json", data)
    return {**plan, "plan_id": digest}


def load_plan(plan_id):
    if not re.fullmatch(r"[a-f0-9]{64}", plan_id):
        raise RoutingError("invalid_plan", "올바른 자동 항로 식별자가 아닙니다.")
    try:
        data = (plans_root() / f"{plan_id}.json").read_bytes()
    except OSError as exc:
        raise RoutingError("plan_not_found", "저장된 자동 항로를 찾을 수 없습니다. 다시 계산하세요.") from exc
    if hashlib.sha256(data).hexdigest() != plan_id:
        raise RoutingError("plan_corrupt", "저장된 자동 항로의 무결성 검사에 실패했습니다.")
    return {**json.loads(data), "plan_id": plan_id}
