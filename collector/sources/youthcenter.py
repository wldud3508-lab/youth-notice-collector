"""온통청년 — 청년정책 Open API (한국고용정보원, 공공데이터포털 15143273).

엔드포인트: GET https://www.youthcenter.go.kr/go/ythip/getPlcy  (apiKeyNm, pageNum, pageSize, rtnType=json)
응답: result.youthPolicyList[], 전체 건수 result.pagging.totCount
엔드포인트·필드명은 공개 프로젝트(youth-policy-mcp, 2026-07 실키 검증 주석)와 대조했고,
이 저장소에서 실키로 호출해 본 적은 아직 없다. 구조가 다르면 실패로 기록한다.
"""
from __future__ import annotations

import os
import time

from .. import textutil as T
from ..models import Item
from .base import FetchResult, Source, SourceInfo, SourceUnavailable, UnexpectedResponse, http_get, redact

API = "https://www.youthcenter.go.kr/go/ythip/getPlcy"
PAGE_SIZE = 100
MAX_PAGES = 100
CLIP = 600


def _clip(s):
    s = T.clean(s)
    return s[:CLIP].rstrip() + " …(이하 생략, 원문 링크 참고)" if s and len(s) > CLIP else s


class YouthCenter(Source):
    info = SourceInfo(
        key="youthcenter", name="온통청년", categories=["생활비", "주거비"], method="공식 API (온통청년 Open API)",
        connection="공식 API (온통청년 청년정책)",
        stage="준비 완료", stage_note="코드 준비됨. 온통청년 Open API 인증키 승인 대기",
    )

    def __init__(self, api_key: str | None = None, include_public_rental: bool = False, http=http_get):
        self.key = (api_key if api_key is not None else os.getenv("YOUTHCENTER_API_KEY") or "").strip() or None
        self.include_public_rental = include_public_rental
        self.http = http

    def _page(self, n: int) -> tuple[list[dict], int | None]:
        try:
            r = self.http(API, params={"apiKeyNm": self.key, "pageNum": n, "pageSize": PAGE_SIZE, "rtnType": "json"})
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(redact(f"{n}쪽 요청 실패: {e}", self.key)) from e
        diag = lambda: redact(f"HTTP {getattr(r, 'status_code', '?')}, 형식 {getattr(r, 'headers', {}).get('Content-Type', '?')}, "
                              f"앞부분 {' '.join((getattr(r, 'text', '') or '')[:120].split())!r}", self.key)
        try:
            body = r.json()
        except ValueError as e:
            raise UnexpectedResponse("JSON이 아닌 응답 — " + diag()) from e
        res = body.get("result") if isinstance(body, dict) else None
        if not isinstance(res, dict) or not isinstance(res.get("youthPolicyList"), list):
            msg = body.get("resultMessage") if isinstance(body, dict) else None
            code = body.get("resultCode") if isinstance(body, dict) else None
            keys = list(body)[:8] if isinstance(body, dict) else type(body).__name__
            if msg and ("인증" in msg or "key" in msg.lower()):
                raise SourceUnavailable("미연결", redact(f"인증키 오류: {msg} (resultCode {code})", self.key))
            raise UnexpectedResponse(redact(f"result.youthPolicyList 없음 (resultCode {code}, resultMessage {msg}, 최상위 키 {keys})", self.key))
        pag = res.get("pagging") or res.get("paging") or {}
        try:
            total = int(pag.get("totCount"))
        except (TypeError, ValueError):
            total = None
        return res["youthPolicyList"], total

    def fetch(self, *, since: str | None = None) -> FetchResult:
        if not self.key:
            raise SourceUnavailable("미연결", "환경변수 YOUTHCENTER_API_KEY 가 없음")
        rows: list[dict] = []
        pages = 0
        total = None
        for n in range(1, MAX_PAGES + 1):
            page, total = self._page(n)
            pages += 1
            rows.extend(page)
            if not page or (total is not None and len(rows) >= total):
                break
            time.sleep(0.3)
        if total is not None and len(rows) < total:
            raise UnexpectedResponse(f"전체 {total}건 중 {len(rows)}건만 받음({pages}쪽) — 페이지 처리 확인 필요")
        self.pages, self.total = pages, total
        print(f"[youthcenter] 응답 확인: {pages}쪽, 전체 {total}건(API 표기), 받은 {len(rows)}건")
        items, skipped = [], {}
        for row in rows:
            item, why = self.map_row(row)
            if item:
                items.append(item)
            else:
                skipped[why] = skipped.get(why, 0) + 1
        return FetchResult(items=items, fetched_raw=len(rows), skipped=skipped)

    def map_row(self, row: dict) -> tuple[Item | None, str]:
        g = lambda *ks: next((T.clean(row.get(k)) for k in ks if T.clean(row.get(k))), None)
        pid, name = g("plcyNo"), g("plcyNm")
        if not pid or not name:
            return None, "정책번호·정책명 없음"
        content, expl = g("plcySprtCn"), g("plcyExplnCn")
        cat, cat_basis = T.classify(name, content, expl, g("plcyKywdNm"), g("mclsfNm"),
                                    include_public_rental=self.include_public_rental)
        if not cat:
            return None, cat_basis or "생활비·주거비·장학금 분류에 해당 없음"

        need, ev = [], {}
        aply = g("aplyYmd")
        if aply:
            period_text = aply
            p = T.parse_period(aply)
            if p.needs_review and not p.always:
                need += ["apply_start", "apply_end"]
                ev["apply_end"] = f"자동 해석 보류: {p.reason}"
        else:
            # 신청기간이 없으면 사업기간을 신청기간으로 쓰지 않는다. 원문만 보존하고 확인 요청.
            bgn, end, etc = g("bizPrdBgngYmd"), g("bizPrdEndYmd"), g("bizPrdEtcCn")
            period_text = f"(사업기간) {bgn or ''} ~ {end or ''}" if (bgn or end) else etc
            p = T.Period(always=bool(period_text and ("상시" in period_text or "연중" in period_text)))
            if not p.always:
                need += ["apply_start", "apply_end"]
            if period_text:
                ev["period_text"] = "신청기간(aplyYmd) 없음 — 사업기간·기타 기간 원문만 표시"

        lo, hi = g("sprtTrgtMinAge"), g("sprtTrgtMaxAge")
        age = None
        if g("sprtTrgtAgeLmtYn") == "N":
            age = "연령 제한 없음 (API 표기)"
        elif lo or hi:
            text_age, text_ev, man_need = T.parse_age(" ".join(x for x in (expl, g("addAplyQlfcCndCn")) if x))
            if text_age:
                age = text_age
                ev["age_condition"] = text_ev
                if man_need:
                    need.append("age_condition")
            else:
                age = f"{lo or '?'}~{hi or '?'}세 (API 수치, 만 나이 여부 원문 확인 필요)"
                need.append("age_condition")

        region, reg_basis = T.region_from_codes(g("zipCd"))
        if not region:
            need.append("region")

        income = g("earnEtcCn")
        if not income and (g("earnMinAmt") or g("earnMaxAmt")):
            need.append("income_condition")
            ev["income_condition"] = f"API 수치 earnMinAmt={g('earnMinAmt')}, earnMaxAmt={g('earnMaxAmt')} (단위 미확인)"

        stype, st_ev = T.support_type(name, content)
        if not stype:
            need.append("support_type")
        elif st_ev:
            ev["support_type"] = st_ev
        amt = T.parse_amount(content)
        if amt.ambiguous:
            need.append("amount_text")
        if amt.evidence:
            ev["amount_text"] = amt.evidence

        extra = {}
        if cat in ("생활비", "주거비"):
            extra = {"amount_period_text": amt.text, "budget_end": None, "household": None,
                     "housing": None, "self_pay": None,
                     "loan_terms": None}
        elif cat == "장학금":
            extra = {"pay_amount": amt.text, "income_cond": income}

        org = g("sprvsnInstCdNm", "operInstCdNm", "rgtrInstCdNm")
        fields = {
            "category": cat, "title": name, "organizer": org,
            "info_type": "상시 제도 안내" if p.always else "모집공고",
            "target_text": _clip(expl), "youth_relevant": True,
            "youth_basis": "온통청년(청년정책 통합플랫폼) 등록 청년정책",
            "age_condition": age, "region": region, "region_condition": reg_basis,
            "income_condition": income, "status_condition": _clip(g("addAplyQlfcCndCn")),
            "benefit_text": _clip(content), "support_type": stype,
            "amount_text": amt.text, "amount_value": amt.value, "amount_unit": amt.unit,
            "amount_period": amt.period, "amount_qualifier": amt.qualifier,
            "apply_start": p.start, "apply_end": p.end, "apply_end_time": p.end_time,
            "period_text": period_text, "apply_method": _clip(g("plcyAplyMthdCn")),
            "apply_url": g("aplyUrlAddr"), "required_docs": _clip(g("sbmsnDcmntCn")),
            "exclusions": _clip(g("ptcpPrpTrgtCn")), "official_url": g("refUrlAddr1", "refUrlAddr2"),
            "attachment_url": None, "posted_at": T.ymd_compact(g("frstRegDt")), "extra": extra,
        }
        ev["category"] = cat_basis or ""
        site_cat = " > ".join(x for x in (g("lclsfNm"), g("mclsfNm")) if x) or None
        # 온통청년 상세 페이지 주소 규칙은 확인하지 못해 만들지 않는다(정책번호로 검색 가능).
        return Item("youthcenter", "온통청년", pid, None, site_cat, fields, ev, need), ""
