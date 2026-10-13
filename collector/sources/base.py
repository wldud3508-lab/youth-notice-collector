"""출처 모듈 공통 부분."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import requests

from ..models import Item

USER_AGENT = "youth-notice-collector/0.1 (personal research; once daily)"


class SourceUnavailable(Exception):
    """연결할 수 없는 상태(인증키 없음, 연결 보류 등). 실패와 구분한다."""

    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status = status   # '미연결' 또는 '연결 보류'


class UnexpectedResponse(Exception):
    """응답 구조가 예상과 다름. 조용히 0건으로 넘기지 않고 실패로 기록한다."""


@dataclass
class FetchResult:
    items: list[Item]
    fetched_raw: int                     # 출처에서 받은 전체 건수(필터 전)
    skipped: dict = field(default_factory=dict)   # 제외 사유별 건수


@dataclass
class SourceInfo:
    key: str
    name: str
    categories: list[str]
    method: str
    connection: str        # 화면에 보이는 연결 상태 설명


class Source:
    info: SourceInfo

    def fetch(self, *, since: str | None) -> FetchResult:  # pragma: no cover - 인터페이스
        raise NotImplementedError


def http_get(url: str, *, params: dict | None = None, timeout: int = 30, retries: int = 3,
             session: requests.Session | None = None, accept: str | None = None) -> requests.Response:
    """GET + 제한적 재시도(5xx·네트워크 오류만). 4xx는 재시도하지 않는다."""
    s = session or requests.Session()
    headers = {"User-Agent": USER_AGENT}
    if accept:
        headers["Accept"] = accept
    last: Exception | None = None
    for attempt in range(retries):
        try:
            r = s.get(url, params=params, headers=headers, timeout=timeout)
            if r.status_code >= 500:
                last = requests.HTTPError(f"HTTP {r.status_code}", response=r)
            else:
                r.raise_for_status()
                return r
        except (requests.ConnectionError, requests.Timeout) as e:
            last = e
        time.sleep(2 * (attempt + 1))
    assert last is not None
    raise last


def redact(msg: str, *secrets: str | None) -> str:
    """오류 메시지에서 인증키를 지운다."""
    for s in secrets:
        if s:
            msg = msg.replace(s, "***")
            from urllib.parse import quote
            msg = msg.replace(quote(s, safe=""), "***")
    return msg
