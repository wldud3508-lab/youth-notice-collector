"""자동수집을 하지 않는 출처(연결 보류). 수동 입력(manual/notices.json)으로만 공고를 넣는다."""
from __future__ import annotations

from .base import FetchResult, Source, SourceInfo, SourceUnavailable


class Held(Source):
    def __init__(self, key: str, name: str, categories: list[str], reason: str):
        self.info = SourceInfo(key=key, name=name, categories=categories, method="수동 입력",
                               connection=f"연결 보류 · {reason}")
        self.reason = reason

    def fetch(self, *, since: str | None = None) -> FetchResult:
        raise SourceUnavailable("연결 보류", self.reason)


def linkareer() -> Held:
    return Held("linkareer", "링커리어", ["공모전"],
                "공개 API 없음, 이용약관이 자동 접속 프로그램 사용과 콘텐츠 복제·배포를 제한 (제휴 문의 필요)")


def wevity() -> Held:
    return Held("wevity", "위비티", ["공모전"],
                "공개 API·RSS 없음, 이용약관상 게시 자료의 지적재산권이 위비티에 있고 가공·상업적 이용 제한 (허락 필요)")
