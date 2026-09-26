"""운영 루프 단위 검증 — 무인 운전의 안전 성질."""

import numpy as np
import pytest

from poseidon.scheduler import operational as op


@pytest.fixture(autouse=True)
def isolated_catalog(monkeypatch, tmp_path):
    from poseidon.core.catalog import Catalog
    cat = Catalog(tmp_path / "operational.sqlite")
    monkeypatch.setattr(op, "Catalog", lambda path: cat)


def test_disk_free_is_positive():
    assert op.disk_free_gb() > 0


def test_loop_survives_step_failure(monkeypatch, caplog):
    """어떤 단계가 던져도 루프는 죽지 않는다 (무인 운전의 핵심 성질)."""
    def boom(*a, **k):
        raise RuntimeError("의도적 실패")
    monkeypatch.setattr(op.ing, "ingest_obs", boom)
    monkeypatch.setattr(op.ing, "find_available_cycle", boom)
    monkeypatch.setattr(op.time, "sleep", lambda s: None)
    monkeypatch.setattr(op, "pending_cycles", lambda *a: [])
    rc = op.loop(hours=24, obs_interval_s=0, poll_s=0, max_iter=2)
    assert rc == 0                                    # 정상 종료


def test_chain_skips_when_disk_below_stop_threshold(monkeypatch):
    monkeypatch.setattr(op, "disk_free_gb", lambda: op.DISK_STOP_GB - 1.0)
    res = op.run_chain("20260101T00", hours=24)
    assert res["ingest"].startswith("skipped-disk")


def test_forecast_coverage_gate(monkeypatch, tmp_path):
    """이미 요구 리드를 덮는 예보가 있으면 재생산하지 않는다."""
    monkeypatch.setattr(op, "disk_free_gb", lambda: 100.0)
    monkeypatch.setattr(op.ing, "run_once", lambda *a, **k: 0)
    monkeypatch.setattr(op.ing, "run_once", lambda *a, **k: 0)
    monkeypatch.setattr(op.asyncio, "run", lambda coro: 0)
    monkeypatch.setattr(op, "inputs_cover", lambda *a: True)
    monkeypatch.setattr(op, "forecast_coverage",
                        lambda c, l: (72.0, frozenset(op.spec.SNAPSHOT_VARS)))
    called = {"n": 0}
    import poseidon.scheduler.wave_cycle as wc
    monkeypatch.setattr(wc, "run_wave_forecast",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1) or {})
    import poseidon.validation.collocate as col
    monkeypatch.setattr(col, "collocate_wave", lambda *a, **k: [])
    res = op.run_chain("20260101T00", hours=72)
    assert res["forecast"].startswith("skipped")
    assert called["n"] == 0                           # 예보를 다시 돌리지 않았다


def test_insufficient_coverage_triggers_rerun(monkeypatch):
    """기존 예보가 24h뿐인데 72h를 요구하면 재생산한다 (2026-08-25 사고 회귀)."""
    monkeypatch.setattr(op, "disk_free_gb", lambda: 100.0)
    monkeypatch.setattr(op.ing, "run_once", lambda *a, **k: 0)
    monkeypatch.setattr(op.asyncio, "run", lambda coro: 0)
    monkeypatch.setattr(op, "inputs_cover", lambda *a: True)
    monkeypatch.setattr(op, "forecast_coverage",
                        lambda c, l: (24.06, frozenset(op.spec.SNAPSHOT_VARS)))
    called = {"n": 0}
    import poseidon.scheduler.wave_cycle as wc
    monkeypatch.setattr(wc, "run_wave_forecast",
                        lambda *a, **k: (called.__setitem__("n", called["n"] + 1), {})[1])
    import poseidon.validation.collocate as col
    monkeypatch.setattr(col, "collocate_wave", lambda *a, **k: [])
    res = op.run_chain("20260101T00", hours=72)
    assert res["forecast"].startswith("ok")
    assert called["n"] == 1                           # 재생산했다


def test_missing_variables_trigger_rerun(monkeypatch):
    """리드는 충분하나 변수가 부족한 예보는 재생산한다.

    파주기·파향 저장부 도입 이전의 hs-only 산출물이 리드만 채웠다는 이유로
    영구히 건너뛰어지는 것을 막는다 — "있는가"가 아니라 "요구를 충족하는가".
    """
    monkeypatch.setattr(op, "disk_free_gb", lambda: 100.0)
    monkeypatch.setattr(op.ing, "run_once", lambda *a, **k: 0)
    monkeypatch.setattr(op.asyncio, "run", lambda coro: 0)
    monkeypatch.setattr(op, "inputs_cover", lambda *a: True)
    monkeypatch.setattr(op, "forecast_coverage",
                        lambda c, l: (72.0, frozenset({"hs"})))   # 옛 포맷
    called = {"n": 0}
    import poseidon.scheduler.wave_cycle as wc
    monkeypatch.setattr(wc, "run_wave_forecast",
                        lambda *a, **k: (called.__setitem__("n", called["n"] + 1), {})[1])
    import poseidon.validation.collocate as col
    monkeypatch.setattr(col, "collocate_wave", lambda *a, **k: [])
    res = op.run_chain("20260101T00", hours=72)
    assert called["n"] == 1                            # 변수 부족 -> 재생산했다
    assert res["forecast"].startswith("ok")


def test_backlog_keeps_old_local_input_and_preserves_existing(tmp_path):
    from poseidon.core.catalog import Catalog
    cat = Catalog(tmp_path / 'catalog.sqlite')
    for label in ('20260101T00', '20260101T06'):
        for collection in ('forcing', 'boundary'):
            cat.register_dataset(collection=collection, domain=op.settings.domain_name,
                                 uri='/unused', cycle=label)
    cat.register_dataset(collection='forecast', domain=op.settings.domain_name,
                         uri='/unused', cycle='20260101T06', source_id='spectral_wave-L1')
    assert op.pending_cycles(cat) == ['20260101T00']


def test_observation_and_network_failure_do_not_block_local_queue(monkeypatch):
    """원천·관측이 죽어도 로컬 큐는 돈다. 실패한 사이클이 다음 사이클을 막지 않는다."""
    def boom(*a, **k):
        raise RuntimeError('offline')
    monkeypatch.setattr(op.ing, 'ingest_obs', boom)
    monkeypatch.setattr(op.ing, 'find_available_cycle', boom)
    monkeypatch.setattr(op, 'pending_cycles', lambda *a: ['20260101T00', '20260101T06'])
    monkeypatch.setattr(op, 'inputs_cover', lambda *a: True)
    called = []
    def run(label, hours, **kwargs):
        called.append(label)
        return {'forecast': 'error' if label.endswith('06') else 'ok'}   # 최신이 실패
    monkeypatch.setattr(op, 'run_chain', run)
    assert op.loop(72, 0, 0, max_iter=1) == 0
    assert called == ['20260101T06', '20260101T00']   # 최신 먼저, 실패해도 다음으로


def test_forecast_order_is_newest_first():
    """2026-09-19: 오래된 순 처리로 6일 전 사이클을 계산하고 있었다(회귀 방지)."""
    assert op.forecast_order(['20260913T00', '20260922T06', '20260915T12']) == \
        ['20260922T06', '20260915T12', '20260913T00']


def test_new_cycle_jumps_ahead_of_backlog(monkeypatch):
    """백로그를 처리하는 중에 새 사이클이 도착하면 다음 반복에서 맨 앞에 선다."""
    monkeypatch.setattr(op.ing, 'ingest_obs', lambda *a, **k: None)
    monkeypatch.setattr(op.ing, 'find_available_cycle', lambda *a, **k: None)
    monkeypatch.setattr(op.time, 'sleep', lambda s: None)
    monkeypatch.setattr(op, 'inputs_cover', lambda *a: True)
    queue = [['20260910T00', '20260910T06', '20260910T12'],
             ['20260910T00', '20260910T06', '20260910T18']]   # 둘째 반복에 T18 도착
    monkeypatch.setattr(op, 'pending_cycles', lambda *a: queue.pop(0))
    done = []
    monkeypatch.setattr(op, 'run_chain', lambda label, h, **k: done.append(label) or {'forecast': 'ok'})
    op.loop(72, 3600, 0, max_iter=2)
    assert done == ['20260910T12', '20260910T18']      # 백로그(T00·T06)보다 새 사이클이 먼저


def test_secure_inputs_oldest_first_and_skips_covered(monkeypatch):
    """입력 확보는 보관 창 가장자리(가장 오래된 것)부터. 이미 있는 입력은 다시 받지 않는다."""
    have = {'20260913T06'}
    monkeypatch.setattr(op, 'inputs_cover', lambda c, label, h: label in have)
    monkeypatch.setattr(op, 'disk_free_gb', lambda: 100.0)
    fetched = []
    async def fake_run_once(steps, label):
        fetched.append(label); have.add(label); return 0
    monkeypatch.setattr(op.ing, 'run_once', fake_run_once)
    out = op.secure_inputs(None, ['20260922T00', '20260913T00', '20260913T06'], 72)
    assert fetched == ['20260913T00', '20260922T00']   # 오래된 것부터, T06 은 건너뜀
    assert out == {'20260913T00': 'ok', '20260922T00': 'ok'}


def test_secure_inputs_failure_is_isolated(monkeypatch):
    monkeypatch.setattr(op, 'inputs_cover', lambda *a: False)
    monkeypatch.setattr(op, 'disk_free_gb', lambda: 100.0)
    async def flaky(steps, label):
        if label.endswith('00'):
            raise RuntimeError('NOMADS 404')
        return 0
    monkeypatch.setattr(op.ing, 'run_once', flaky)
    out = op.secure_inputs(None, ['20260913T00', '20260913T06'], 72)
    assert out['20260913T00'].startswith('error') and '20260913T06' in out


def test_loop_secures_inputs_before_forecasting(monkeypatch):
    """원천에 닿을 때는 예보보다 입력 확보가 먼저다(보관 창 유실 방지)."""
    order = []
    monkeypatch.setattr(op.ing, 'ingest_obs', lambda *a, **k: None)
    monkeypatch.setattr(op.ing, 'find_available_cycle', lambda *a, **k: None)
    monkeypatch.setattr(op.asyncio, 'run', lambda coro: (coro.close() if hasattr(coro, 'close') else None) or object())
    monkeypatch.setattr(op, 'pending_cycles', lambda *a: ['20260913T00'])
    monkeypatch.setattr(op, 'secure_inputs', lambda *a: order.append('secure') or {})
    monkeypatch.setattr(op, 'inputs_cover', lambda *a: True)
    monkeypatch.setattr(op, 'run_chain', lambda *a, **k: order.append('forecast') or {'forecast': 'ok'})
    op.loop(72, 3600, 0, max_iter=1)
    assert order == ['secure', 'forecast']


def test_forcing_alone_is_not_sufficient(tmp_path):
    from poseidon.core.catalog import Catalog
    import xarray as xr
    cat = Catalog(tmp_path / 'inputs.sqlite')
    def register(collection, hours):
        path = tmp_path / (collection + str(hours) + '.zarr')
        times = np.datetime64('2026-01-01') + np.arange(0, hours + 1, 3).astype('timedelta64[h]')
        xr.Dataset(coords={'time': times}).to_zarr(path)
        cat.register_dataset(collection=collection, domain=op.settings.domain_name,
                             uri=str(path), cycle='20260101T00')
    register('forcing', 72)
    assert not op.inputs_cover(cat, '20260101T00', 72)
    register('boundary', 24)
    assert not op.inputs_cover(cat, '20260101T00', 72)
    register('boundary', 72)
    assert op.inputs_cover(cat, '20260101T00', 72)
