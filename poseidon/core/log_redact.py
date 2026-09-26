"""로그에서 인증키를 가린다.

httpx 는 INFO 수준에서 요청 URL 전체를 기록한다("HTTP Request: GET <url> ...").
기상청 API허브는 인증키를 쿼리 파라미터(authKey=)로, 바다누리는 ServiceKey= 로 받는다.
그래서 가리지 않은 채 운영 루프를 돌리면 키가 로그 파일에 그대로 쌓인다.
2026-09-11 인수인계 준비 중 data/operational.log 에서 기상청 키 150건을 발견해 가렸다.

키를 쓰는 어댑터(kma, khoa)가 import 될 때 install() 을 부른다. 여러 번 불러도 한 번만 붙는다.
필터는 'httpx' 로거 자체에 붙는다 — 로거 필터는 그 로거가 직접 남긴 레코드에 적용되고,
httpx 는 요청 로그를 logging.getLogger("httpx") 로 남긴다. basicConfig 보다 먼저 붙어도 된다.
"""

from __future__ import annotations

import logging
import re

MASK = "<REDACTED>"
_PAT = re.compile(r"((?:authKey|serviceKey|ServiceKey)=)[^&\s\"']+")
_LOGGERS = ("httpx", "httpcore")


def redact(text: str) -> str:
    """쿼리 문자열의 인증키 값을 MASK 로 바꾼다. 다른 파라미터는 그대로 둔다."""
    return _PAT.sub(r"\1" + MASK, text)


class RedactFilter(logging.Filter):
    """레코드를 버리지 않고, 완성된 메시지에서 키만 가린다."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # 포맷 실패 레코드는 건드리지 않는다
            return True
        red = redact(msg)
        if red != msg:
            record.msg = red
            record.args = None
        return True


def install() -> None:
    """httpx·httpcore 로거에 필터를 한 번만 붙인다."""
    for name in _LOGGERS:
        lg = logging.getLogger(name)
        if not any(isinstance(f, RedactFilter) for f in lg.filters):
            lg.addFilter(RedactFilter())
