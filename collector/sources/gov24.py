"""정부24(보조금24) — 행정안전부_대한민국 공공서비스(혜택) 정보 (공공데이터포털 15113968).

방식: 공식 Open API (api.odcloud.kr/api/gov24/v3). 인증키: 공공데이터포털 활용신청 후 발급.
명세(Swagger 2.0, infuser.odcloud.kr/api/stages/44436/api-docs)와 대조함:
  - 경로: /gov24/v3/serviceList, /serviceDetail, /supportConditions
  - 인증: 쿼리 파라미터 serviceKey (명세의 ApiKeyAuth2. 헤더 Authorization 방식도 명세에 있으나 사용 안 함)
  - 파라미터: page, perPage, returnType=JSON. perPage 상한은 명세에 없음
  - 응답: page, perPage, totalCount, currentCount, matchCount, data[]
이 저장소에서 실키로 호출해 본 적은 아직 없다.
첫 실행에서 응답 구조가 다르면 UnexpectedResponse로 '실패' 처리한다(0건으로 위장하지 않음).
"""
from __future__ import annotations

import os
import re
import time
from urllib.parse import unquote

from .. import textutil as T
from ..models import Item
from .base import FetchResult, Source, SourceInfo, SourceUnavailable, UnexpectedResponse, http_get, redact

API = "https://api.odcloud.kr/api/gov24/v3"
PER_PAGE = 1000
MAX_PAGES = 40
CLIP = 600


def _clip(s: str | None) -> str | None:
    s = T.clean(s)
    if s and len(s) > CLIP:
        return s[:CLIP].rstrip() + " …(이하 생략, 원문 링크 참고)"
    return s


def _sentence(text: str | None, pat: str) -> str | None:
    t = T.clean(text)
    if not t:
        return None
    m = re.search(pat, t)
    return T.excerpt(t, m.start(), m.end(), 80) if m else None


class Gov24(Source):
    info = SourceInfo(
        key="gov24", name="정부24", categories=["생활비", "주거비"], method="공식 API (공공데이터포털)",
        connection="연결됨 · 공식 API (공공데이터포털 15113968)",
    )

    def __init__(self, service_key: str | None = None, include_public_rental: bool = False, http=http_get):
        raw = service_key if service_key is not None else os.getenv("DATA_GO_KR_SERVICE_KEY")
        self.key = unquote(raw.strip()) if raw else None   # 인코딩 키를 넣어도 디코딩해서 사용
        self.include_public_rental = include_public_rental
        self.http = http

    # ------------------------------------------------------------ 수집
    def _fetch_all(self, op: str) -> list[dict]:
        rows: list[dict] = []
        for page in range(1, MAX_PAGES + 1):
            try:
                r = self.http(f"{API}/{op}", params={"serviceKey": self.key, "page": page,
                                                    "perPage": PER_PAGE, "returnType": "JSON"})
            except Exception as e:  # noqa: BLE001
                status = getattr(getattr(e, "response", None), "status_code", None)
                if status in (401, 403):
                    raise SourceUnavailable("미연결", "인증키가 승인되지 않았거나 잘못됨 (HTTP %s)" % status) from e
                raise RuntimeError(redact(f"{op} {page}쪽 요청 실패: {e}", self.key)) from e
            try:
                body = r.json()
            except ValueError as e:
                raise UnexpectedResponse(f"{op}: JSON이 아닌 응답") from e
            data = body.get("data") if isinstance(body, dict) else None
            if not isinstance(data, list):
                raise UnexpectedResponse(f"{op}: 응답에 data 배열이 없음 (키: {list(body)[:6] if isinstance(body, dict) else type(body).__name__})")
            rows.extend(data)
            total = body.get("totalCount") or body.get("matchCount")
            if not data or (isinstance(total, int) and len(rows) >= total):
                break
            # 명세에 perPage 상한이 적혀 있지 않다. 서버가 요청보다 적게 주면 다음 쪽 계산이 어긋나 누락될 수 있으므로
            # 조용히 넘기지 않고 실패로 기록한다.
            if len(data) < PER_PAGE:
                raise UnexpectedResponse(f"{op}: {page}쪽에서 {PER_PAGE}건을 요청했는데 {len(data)}건만 옴(전체 {total}건) — 페이지 크기 상한 확인 필요")
            time.sleep(0.3)
        return rows

    def fetch(self, *, since: str | None = None) -> FetchResult:
        if not self.key:
            raise SourceUnavailable("미연결", "환경변수 DATA_GO_KR_SERVICE_KEY 가 없음")
        lst = self._fetch_all("serviceList")
        if lst and "서비스ID" not in lst[0]:
            raise UnexpectedResponse("serviceList 항목에 '서비스ID' 필드가 없음 — API 구조 변경 여부 확인 필요")
        details = {d.get("서비스ID"): d for d in self._fetch_all("serviceDetail")}
        conds = {c.get("서비스ID"): c for c in self._fetch_all("supportConditions")}
        items, skipped = [], {}
        for row in lst:
            item, why = self.map_row(row, details.get(row.get("서비스ID")), conds.get(row.get("서비스ID")))
            if item:
                items.append(item)
            else:
                skipped[why] = skipped.get(why, 0) + 1
        return FetchResult(items=items, fetched_raw=len(lst), skipped=skipped)

    # ------------------------------------------------------------ 변환
    def map_row(self, row: dict, detail: dict | None, cond: dict | None) -> tuple[Item | None, str]:
        g = lambda k: T.clean((detail or {}).get(k)) or T.clean(row.get(k))  # 상세 우선
        sid = T.clean(row.get("서비스ID"))
        name = g("서비스명")
        if not sid or not name:
            return None, "서비스ID·서비스명 없음"
        target, criteria, content = g("지원대상"), g("선정기준"), g("지원내용")

        youth, basis = T.youth_relevance(name, target, criteria)
        def _int(v):   # 명세에 값의 형식이 적혀 있지 않아 숫자·숫자 문자열을 모두 받음
            try:
                return int(v) if v is not None and str(v).strip() != "" else None
            except (TypeError, ValueError):
                return None
        a0 = _int(cond.get("JA0110")) if cond else None
        a1 = _int(cond.get("JA0111")) if cond else None
        if not youth and isinstance(a0, int) and isinstance(a1, int) and 15 <= a0 and a1 <= 39:
            youth, basis = True, f"보조금24 지원조건 연령 코드 {a0}~{a1}세"
        if not youth and cond and cond.get("JA0320") == "Y" and sum(1 for k in ("JA0317", "JA0318", "JA0319", "JA0320", "JA0326", "JA0327") if cond.get(k) == "Y") <= 2:
            youth, basis = True, "보조금24 지원조건 코드: 대학(원)생"
        if not youth:
            return None, "청년·대학생 대상 아님"

        cat, cat_basis = T.classify(name, content, g("서비스분야"), include_public_rental=self.include_public_rental)
        if not cat:
            return None, cat_basis or "생활비·주거비·장학금 분류에 해당 없음"

        need: list[str] = []
        ev: dict[str, str] = {}
        period_text = g("신청기한")
        p = T.parse_period(period_text)
        if p.needs_review and not p.always:
            need += ["apply_start", "apply_end"]
        if period_text:
            ev["period_text"] = period_text[:160]
        if p.reason:
            ev["apply_end"] = f"자동 해석 보류: {p.reason}"

        age, age_ev, age_need = T.parse_age(" ".join(x for x in (target, criteria) if x))
        if not age and isinstance(a0, int) and isinstance(a1, int) and (a0 > 0 or a1 < 100):
            age, age_ev, age_need = f"{a0}~{a1}세 (지원조건 코드 기준, 만 나이 여부 원문 확인 필요)", "supportConditions JA0110/JA0111", True
        if age_need:
            need.append("age_condition")
        if age_ev:
            ev["age_condition"] = age_ev

        stype, st_ev = T.support_type(name, content, g("지원유형"))
        if not stype:
            need.append("support_type")
        elif st_ev:
            ev["support_type"] = st_ev
        amt = T.parse_amount(content)
        if amt.ambiguous:
            need.append("amount_text")
        if amt.evidence:
            ev["amount_text"] = amt.evidence

        org = g("소관기관명")
        region, reg_basis = T.region_from_org(org)
        if not region:
            need.append("region")
        income = _sentence(criteria, r"중위소득|소득|재산")
        if income:
            ev["income_condition"] = income

        extra = {}
        if cat in ("생활비", "주거비"):
            extra = {
                "amount_period_text": amt.text,
                "budget_end": _sentence(" ".join(x for x in (content, criteria, period_text) if x), r"예산\s*(?:소진|범위)"),
                "household": _sentence(criteria, r"가구"),
                "housing": _sentence(" ".join(x for x in (criteria, target) if x), r"무주택|보증금|월세|임차"),
                "self_pay": _sentence(content, r"본인\s*부담|자부담"),
                "loan_terms": _sentence(content, r"금리|한도|상환|이자") if stype in ("대출", "이자지원") else None,
            }
        elif cat == "장학금":
            extra = {"pay_amount": amt.text, "income_cond": income}

        fields = {
            "category": cat, "title": name,
            "organizer": " ".join(x for x in (org, g("부서명")) if x) or None,
            "info_type": "상시 제도 안내" if p.always else "모집공고",
            "target_text": _clip(target), "youth_relevant": True, "youth_basis": basis,
            "age_condition": age, "region": region,
            "region_condition": reg_basis, "income_condition": income, "status_condition": None,
            "benefit_text": _clip(content), "support_type": stype,
            "amount_text": amt.text, "amount_value": amt.value, "amount_unit": amt.unit,
            "amount_period": amt.period, "amount_qualifier": amt.qualifier,
            "apply_start": p.start, "apply_end": p.end, "apply_end_time": p.end_time,
            "period_text": period_text, "apply_method": _clip(g("신청방법")),
            "apply_url": g("온라인신청사이트URL"), "required_docs": _clip(g("구비서류")),
            "exclusions": _sentence(criteria, r"제외|중복\s*(?:수혜|지원)|불가"),
            "official_url": None, "attachment_url": None,
            "posted_at": T.ymd_compact(row.get("등록일시")),
            "extra": extra,
        }
        ev["category"] = cat_basis or ""
        site_cat = " / ".join(x for x in (g("서비스분야"), g("지원유형")) if x) or None
        url = g("상세조회URL")   # 없으면 주소를 추측해 만들지 않는다
        return Item("gov24", "정부24", sid, url, site_cat, fields, ev, need), ""
