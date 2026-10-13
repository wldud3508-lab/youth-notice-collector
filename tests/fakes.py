"""테스트 전용 가짜 응답. 실제 출처에서 받은 데이터가 아니며, 필드 이름만 각 API 문서·공개 코드와 맞췄다.
값은 모두 'TEST' 표시가 들어간 가상 값이다."""
from __future__ import annotations


class FakeResp:
    def __init__(self, data=None, text=None, status=200):
        self._data, self.text, self.status_code = data, text or "", status

    def json(self):
        if self._data is None:
            raise ValueError("not json")
        return self._data


def gov24_row(**kw):
    row = {
        "서비스ID": "TEST000001", "지원유형": "현금", "서비스명": "[TEST] 청년 월세 지원",
        "서비스목적요약": "TEST", "지원대상": "만 19세 ~ 34세 무주택 청년",
        "선정기준": "청년가구 기준 중위소득 60% 이하. 주거급여 수급자 제외",
        "지원내용": "월 최대 20만 원, 최대 12개월 지급 (예산 소진 시 조기 종료)",
        "신청방법": "온라인 신청", "신청기한": "2026. 10. 1. ~ 2026. 10. 31.",
        "상세조회URL": "https://www.gov.kr/portal/rcvfvrSvc/dtlEx/TEST000001",
        "소관기관코드": "0000000", "소관기관명": "국토교통부", "부서명": "TEST과", "조회수": 1,
        "소관기관유형": "중앙행정기관", "사용자구분": "개인", "서비스분야": "주거·자립", "접수기관": "TEST",
        "전화문의": "TEST", "등록일시": "20260901090000", "수정일시": "20260915103320",
    }
    row.update(kw)
    return row


def gov24_http(list_rows, detail_rows=None, cond_rows=None):
    pages = {"serviceList": list_rows, "serviceDetail": detail_rows or [], "supportConditions": cond_rows or []}
    calls = []

    def http(url, params=None, **_):
        op = url.rsplit("/", 1)[1]
        calls.append((op, params.get("page")))
        rows = pages[op]
        start = (params["page"] - 1) * params["perPage"]
        chunk = rows[start:start + params["perPage"]]
        return FakeResp({"page": params["page"], "perPage": params["perPage"], "totalCount": len(rows),
                         "currentCount": len(chunk), "matchCount": len(rows), "data": chunk})
    http.calls = calls
    return http


def youth_row(**kw):
    row = {
        "plcyNo": "TEST-2026-001", "plcyNm": "[TEST] 청년 구직활동 지원금",
        "plcyExplnCn": "만 19~34세 미취업 청년에게 구직활동 지원금 지급", "plcySprtCn": "월 50만 원씩 최대 6개월 지급",
        "lclsfNm": "일자리", "mclsfNm": "취업", "plcyKywdNm": "보조금",
        "sprvsnInstCdNm": "TEST광역시", "aplyYmd": "20261005 ~ 20261024",
        "sprtTrgtMinAge": "19", "sprtTrgtMaxAge": "34", "sprtTrgtAgeLmtYn": "Y",
        "zipCd": "26110,26140", "earnEtcCn": "기준 중위소득 150% 이하",
        "plcyAplyMthdCn": "온라인 신청", "aplyUrlAddr": "https://example.com/TEST-apply",
        "sbmsnDcmntCn": "주민등록초본", "frstRegDt": "2026-09-30 10:00:00",
    }
    row.update(kw)
    return row


RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>TEST FEED</title>
<item><title>[TEST] 저소득층 대학생 120만 원 생활비 지원: TEST장학재단 장학금</title>
<link>https://campuscholar.com/test-1/</link><guid>https://campuscholar.com/?p=1</guid>
<pubDate>Mon, 12 Oct 2026 08:22:00 +0000</pubDate>
<category>2월 장학금</category><category>대학 2학년 장학금</category><category>생활비 장학금</category><category>저소득층 지원 장학금</category></item>
<item><title>[TEST] 고등학생 전용 장학금: TEST고교재단</title>
<link>https://campuscholar.com/test-2/</link><guid>https://campuscholar.com/?p=2</guid>
<pubDate>Sun, 11 Oct 2026 08:22:00 +0000</pubDate><category>고등학생 장학금</category></item>
</channel></rss>"""
