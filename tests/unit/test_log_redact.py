"""로그 키 가림 — 2026-09-11 운영 로그에서 기상청 키 150건이 발견된 뒤 추가."""

import importlib
import logging

import pytest

from poseidon.core.log_redact import MASK, RedactFilter, install, redact

pytestmark = pytest.mark.unit


def _strip():
    lg = logging.getLogger("httpx")
    lg.filters = [f for f in lg.filters if not isinstance(f, RedactFilter)]
    return lg


def test_redact_kma_authkey_keeps_other_params():
    s = 'GET https://apihub.kma.go.kr/x.php?tm1=1&authKey=ABCdef_123-xyz&help=0 "HTTP/1.1 200 OK"'
    out = redact(s)
    assert "ABCdef_123-xyz" not in out
    assert f"authKey={MASK}" in out and "tm1=1" in out and "help=0" in out


def test_redact_khoa_service_key():
    out = redact("https://h/p?ServiceKey=SECRET%2Bx&ResultType=json")
    assert "SECRET" not in out and "ResultType=json" in out


def test_httpx_logger_output_is_redacted(caplog):
    lg = _strip()
    install()
    install()  # 두 번 불러도 필터는 하나
    assert sum(isinstance(f, RedactFilter) for f in lg.filters) == 1
    with caplog.at_level(logging.INFO, logger="httpx"):
        lg.info('HTTP Request: %s %s "%s %d %s"',
                 "GET", "https://h/p?authKey=TOPSECRET&a=1", "HTTP/1.1", 200, "OK")
    assert "TOPSECRET" not in caplog.text and MASK in caplog.text


@pytest.mark.parametrize("mod", ["poseidon.ingest.adapters.kma", "poseidon.ingest.adapters.khoa"])
def test_key_adapters_install_filter_on_import(mod):
    lg = _strip()
    importlib.reload(importlib.import_module(mod))
    assert any(isinstance(f, RedactFilter) for f in lg.filters)
