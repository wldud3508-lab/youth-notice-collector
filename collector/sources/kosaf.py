"""한국장학재단 — 한국장학재단_학자금지원정보(대학생) (공공데이터포털 15028252, 파일데이터).

공공데이터포털 파일데이터는 활용신청하면 odcloud 자동변환 API 주소를 준다. 그 주소(경로)는 데이터셋 버전마다
'uddi:...' 형태로 달라 미리 알 수 없으므로, 활용신청 화면의 Swagger에서 경로를 복사해 KOSAF_ODCLOUD_PATH 에 넣는다.
열 이름은 확인하지 못했다. 필수 열(상품명·운영기관명)이 없으면 실제 열 목록을 오류로 남기고 실패 처리한다.
이 데이터는 반년~1년 간격으로 갱신되는 장학상품 목록이며, 매일 새 공고가 올라오는 게시판이 아니다.
"""
from __future__ import annotations

import os
import re
import time
from urllib.parse import unquote

from .. import textutil as T
from ..models import Item
from .base import FetchResult, Source, SourceInfo, SourceUnavailable, UnexpectedResponse, http_get, redact

BASE = "https://api.odcloud.kr"
CLIP = 400


def _pick(row: dict, *names: str):
    for n in names:
        for k in row:
            if k.replace(" ", "") == n.replace(" ", ""):
                v = T.clean(row[k])
                if v and v not in ("-", "해당없음"):
                    return v
    return None


def _clip(s):
    return s[:CLIP] + " …(이하 생략)" if s and len(s) > CLIP else s


class Kosaf(Source):
    info = SourceInfo(
        key="kosaf", name="한국장학재단", categories=["장학금"], method="공공데이터 파일 자동변환 API",
        connection="연결됨 · 공공데이터포털 15028252 (학자금지원정보)",
    )

    def __init__(self, service_key: str | None = None, path: str | None = None, http=http_get):
        raw = service_key if service_key is not None else os.getenv("DATA_GO_KR_SERVICE_KEY")
        self.key = unquote(raw.strip()) if raw else None
        self.path = (path if path is not None else os.getenv("KOSAF_ODCLOUD_PATH") or "").strip() or None
        self.http = http

    def fetch(self, *, since: str | None = None) -> FetchResult:
        if not self.key:
            raise SourceUnavailable("미연결", "환경변수 DATA_GO_KR_SERVICE_KEY 가 없음")
        if not self.path:
            raise SourceUnavailable("미연결", "환경변수 KOSAF_ODCLOUD_PATH 가 없음 (활용신청 후 Swagger의 API 경로)")
        rows: list[dict] = []
        for page in range(1, 30):
            try:
                r = self.http(BASE + self.path, params={"serviceKey": self.key, "page": page, "perPage": 1000, "returnType": "JSON"})
                body = r.json()
            except ValueError as e:
                raise UnexpectedResponse("JSON이 아닌 응답") from e
            except Exception as e:  # noqa: BLE001
                raise RuntimeError(redact(f"{page}쪽 요청 실패: {e}", self.key)) from e
            data = body.get("data") if isinstance(body, dict) else None
            if not isinstance(data, list):
                raise UnexpectedResponse("응답에 data 배열이 없음")
            rows.extend(data)
            total = body.get("totalCount") or body.get("matchCount")
            if not data or (isinstance(total, int) and len(rows) >= total):
                break
            time.sleep(0.3)
        if rows and not (_pick(rows[0], "상품명") and _pick(rows[0], "운영기관명")):
            raise UnexpectedResponse("필수 열(상품명·운영기관명)을 찾지 못함. 실제 열: " + ", ".join(list(rows[0])[:25]))
        items, skipped = [], {}
        for row in rows:
            it, why = self.map_row(row)
            if it:
                items.append(it)
            else:
                skipped[why] = skipped.get(why, 0) + 1
        return FetchResult(items=items, fetched_raw=len(rows), skipped=skipped)

    def map_row(self, row: dict) -> tuple[Item | None, str]:
        name, org = _pick(row, "상품명"), _pick(row, "운영기관명")
        if not name or not org:
            return None, "상품명·운영기관명 없음"
        start = T.ymd_compact(_pick(row, "모집시작일"))
        end = T.ymd_compact(_pick(row, "모집종료일"))
        raw_start, raw_end = _pick(row, "모집시작일"), _pick(row, "모집종료일")
        need, ev = [], {}
        if not start or not end:
            need += ["apply_start", "apply_end"]
        period_text = f"{raw_start or '미기재'} ~ {raw_end or '미기재'}" if (raw_start or raw_end) else None
        if period_text:
            ev["period_text"] = f"모집시작일='{raw_start}', 모집종료일='{raw_end}'"
        benefit = _pick(row, "지원내역 상세내용", "지원내역")
        amt = T.parse_amount(benefit)
        if amt.ambiguous:
            need.append("amount_text")
        if amt.evidence:
            ev["amount_text"] = amt.evidence
        region_txt = _pick(row, "지역거주여부 상세내용", "지역거주여부")
        region = None
        if region_txt:
            for nm, pat in T.SIDO:
                if re.search(pat, region_txt):
                    region = nm
                    break
            if not region:
                need.append("region")
        else:
            region = None
            need.append("region")
        age, age_ev, age_need = T.parse_age(_pick(row, "특정자격 상세내용", "자격제한 상세내용"))
        if age_need:
            need.append("age_condition")
        if age_ev:
            ev["age_condition"] = age_ev
        kind = _pick(row, "학자금유형구분")
        fields = {
            "category": "장학금", "title": name, "organizer": org, "info_type": "모집공고",
            "target_text": _clip(" / ".join(x for x in (_pick(row, "대학구분"), _pick(row, "학년구분"), _pick(row, "학과구분"), _pick(row, "특정자격 상세내용")) if x) or None),
            "youth_relevant": True, "youth_basis": "한국장학재단 학자금지원정보(대학생) 데이터셋",
            "age_condition": age, "region": region, "region_condition": _clip(region_txt),
            "income_condition": _clip(_pick(row, "소득기준 상세내용")), "status_condition": _pick(row, "학년구분"),
            "benefit_text": _clip(benefit), "support_type": "무상지원" if (kind and "장학" in kind) else None,
            "amount_text": amt.text, "amount_value": amt.value, "amount_unit": amt.unit,
            "amount_period": amt.period, "amount_qualifier": amt.qualifier,
            "apply_start": start, "apply_end": end, "period_text": period_text,
            "apply_method": _clip(_pick(row, "선발방법 상세내용")), "required_docs": _clip(_pick(row, "제출서류 상세내용")),
            "exclusions": _clip(_pick(row, "자격제한 상세내용")), "official_url": _pick(row, "홈페이지 주소", "홈페이지주소"),
            "extra": {
                "sch_kind": kind, "pay_amount": amt.text, "pay_freq": None,
                "grade_cond": _clip(_pick(row, "성적기준 상세내용")), "year_cond": _pick(row, "학년구분"),
                "income_cond": _clip(_pick(row, "소득기준 상세내용")), "dup_allowed": None,
            },
        }
        if not fields["support_type"]:
            need.append("support_type")
        sid = "|".join([org, name, raw_start or ""])
        return Item("kosaf", "한국장학재단", sid, None, kind, fields, ev, need), ""
