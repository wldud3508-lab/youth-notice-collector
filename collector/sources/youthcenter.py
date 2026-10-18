"""온통청년 — 청년정책 Open API (한국고용정보원, 공공데이터포털 15143273).

엔드포인트: GET https://www.youthcenter.go.kr/go/ythip/getPlcy  (apiKeyNm, pageNum, pageSize, rtnType=json)
응답: result.youthPolicyList[], 전체 건수 result.pagging.totCount
엔드포인트·필드명은 공개 프로젝트(youth-policy-mcp, 2026-07 실키 검증 주석)와 대조했고,
2026-10-16 시험 실행(실키)에서 33쪽·3264건 전체 수신을 확인했다. 구조가 다르면 실패로 기록한다.
수집 범위 판정은 youthcenter_rules.decide(포함/분류 검토/제외 + 근거). '분류 검토'·'제외'는 DB에 저장하지 않고
시험 보고서(collector/review_report.py)에 사유와 함께 남긴다.
"""
from __future__ import annotations

import os
import re
import time

from .. import rules as R
from .. import textutil as T
from ..models import Item
from .base import FetchResult, Source, SourceInfo, SourceUnavailable, UnexpectedResponse, http_get, redact
from . import youthcenter_rules as YR
from .youthcenter_rules import INCLUDE, Decision, decide

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
        self.rows: list[dict] = []
        self.decisions: list = []
        self.dup_ids: dict = {}
        self.page_log: list[dict] = []
        self.pages = self.total = None

    def _page(self, n: int) -> tuple[list[dict], int | None]:
        try:
            r = self.http(API, params={"apiKeyNm": self.key, "pageNum": n, "pageSize": PAGE_SIZE, "rtnType": "json"})
        except Exception as e:  # noqa: BLE001
            # HTTP 오류여도 응답 본문(결과 코드·메시지)을 남겨 원인을 좁힌다. 주소·키는 남기지 않는다.
            resp = getattr(e, "response", None)
            if resp is not None:
                body = " ".join((getattr(resp, "text", "") or "")[:300].split())
                raise RuntimeError(redact(f"{n}쪽 요청 실패: HTTP {resp.status_code}, 형식 "
                                          f"{resp.headers.get('Content-Type', '?')}, 응답 본문 앞부분 {body!r}", self.key)) from e
            raise RuntimeError(redact(f"{n}쪽 요청 실패: {type(e).__name__}", self.key)) from e
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
        self.page_log.append({"page": n, "http": getattr(r, "status_code", None), "resultCode": body.get("resultCode"),
                              "resultMessage": body.get("resultMessage"), "count": len(res["youthPolicyList"]),
                              "totCount": total, "pagging": {k: v for k, v in pag.items() if k != "apiKeyNm"}})
        print(redact(f"[youthcenter] {n}쪽: HTTP {self.page_log[-1]['http']}, resultCode {body.get('resultCode')}, "
                     f"resultMessage {body.get('resultMessage')!r}, 받은 {len(res['youthPolicyList'])}건, "
                     f"API 전체 {total}건, 페이지 정보 {self.page_log[-1]['pagging']}", self.key))
        return res["youthPolicyList"], total

    def fetch(self, *, since: str | None = None) -> FetchResult:
        if not self.key:
            raise SourceUnavailable("미연결", "환경변수 YOUTHCENTER_API_KEY 가 없음")
        rows: list[dict] = []
        pages = 0
        total = None
        self.page_log = []
        for n in range(1, MAX_PAGES + 1):
            try:
                page, total = self._page(n)
            except SourceUnavailable:
                raise
            except UnexpectedResponse as e:
                # 앞 쪽까지 받은 것을 성공으로 처리하지 않는다. 누적 정보를 오류에 함께 남긴다.
                raise UnexpectedResponse(f"{e} / 앞서 받은 {len(rows)}건(API 전체 {total}건, {pages}쪽) — 일부만 받아 실패 처리") from e
            except Exception as e:  # noqa: BLE001
                raise RuntimeError(f"{e} / 앞서 받은 {len(rows)}건(API 전체 {total}건, {pages}쪽) — 일부만 받아 실패 처리") from e
            pages += 1
            rows.extend(page)
            if not page or (total is not None and len(rows) >= total):
                break
            time.sleep(0.3)
        if total is not None and len(rows) < total:
            raise UnexpectedResponse(f"전체 {total}건 중 {len(rows)}건만 받음({pages}쪽) — 페이지 처리 확인 필요")
        self.pages, self.total = pages, total
        print(f"[youthcenter] 응답 확인: {pages}쪽, 전체 {total}건(API 표기), 받은 {len(rows)}건")
        self.rows = rows
        return self.classify_rows(rows)

    def classify_rows(self, rows: list[dict]) -> FetchResult:
        """받은 원본 행 → 판정·정리. 원본 스냅숏으로 재검토할 때도 같은 함수를 쓴다(재호출 없음)."""
        items, skipped = [], {}
        self.decisions: list[tuple[dict, Decision, Item | None]] = []
        # 같은 정책번호가 응답 안에 여러 번 나오면 첫 행만 저장(같은 실행에서 '변경'으로 잘못 잡히지 않게)
        by_id: dict[str, list[dict]] = {}
        for row in rows:
            by_id.setdefault(T.clean(row.get("plcyNo")) or "", []).append(row)
        self.dup_ids = {k: len(v) for k, v in by_id.items() if k and len(v) > 1}
        done = set()
        for row in rows:
            pid = T.clean(row.get("plcyNo")) or ""
            if pid and pid in done:
                skipped["같은 정책번호 중복 행(첫 행만 저장)"] = skipped.get("같은 정책번호 중복 행(첫 행만 저장)", 0) + 1
                continue
            done.add(pid)
            dec = decide(row, include_public_rental=self.include_public_rental)
            item = None
            if dec.verdict == INCLUDE:
                item = self._item(row, dec)
                if pid in self.dup_ids:
                    same = all(_raw(r) == _raw(row) for r in by_id[pid])
                    item.fields["review_notes"].append({"field": "source", "note": (
                        f"응답 안에 같은 정책번호가 {self.dup_ids[pid]}번 나옴 — "
                        + ("내용 동일" if same else "내용이 서로 달라 첫 행만 저장, 원문 확인 필요"))})
                    if not same:
                        item.needs_review.append("중복 여부")
                items.append(item)
            else:
                key = f"{dec.verdict}: {dec.reason}"
                skipped[key] = skipped.get(key, 0) + 1
            self.decisions.append((row, dec, item))
        return FetchResult(items=items, fetched_raw=len(rows), skipped=skipped)

    def map_row(self, row: dict) -> tuple[Item | None, str]:
        dec = decide(row, include_public_rental=self.include_public_rental)
        if dec.verdict != INCLUDE:
            return None, f"{dec.verdict}: {dec.reason}"
        return self._item(row, dec), ""

    def _item(self, row: dict, dec: Decision) -> Item:
        g = lambda *ks: next((T.clean(row.get(k)) for k in ks if T.clean(row.get(k))), None)
        pid, name = g("plcyNo"), g("plcyNm")
        content, expl = g("plcySprtCn"), g("plcyExplnCn")
        cat = dec.category
        need: list[str] = []
        notes: list[dict] = []
        conflicts: list[dict] = []
        ev: dict[str, str] = {"category": dec.basis_text()}

        # ---- 신청기간: 신청기간(aplyYmd)에만 공통 기간 규칙을 적용. 사업기간은 신청기간으로 쓰지 않음
        aply = g("aplyYmd")
        pr = R.classify_period(aply)
        if aply:
            ev["period_text"] = f"신청기간(aplyYmd) 원문: {aply[:160]}"
        else:
            ev["period_text"] = "신청기간(aplyYmd) 비어 있음 — 날짜를 추측하지 않음"
        bgn, end, etc = g("bizPrdBgngYmd"), g("bizPrdEndYmd"), g("bizPrdEtcCn")
        if bgn or end or etc:
            biz = " ~ ".join(x for x in (T.ymd_compact(bgn) or bgn, T.ymd_compact(end) or end) if x)
            notes.append({"field": "period_text", "note": "사업기간(신청기간 아님, 접수 여부 판단에 쓰지 않음): "
                          + " / ".join(x for x in (biz, etc) if x)})
        aux = None
        if not aply:
            aux = _aux_deadline({"plcySprtCn": content, "plcyAplyMthdCn": g("plcyAplyMthdCn")})
            if aux:
                # 신청기간 필드가 비어 있어 지원내용·신청방법의 기한 문장을 보조로 분류. 날짜는 계산하지 않음
                pr.kind, pr.reason = R.KIND_EVENT, f"{aux['field_label']}의 기한 문장(보조 추출)"
                ev["period_text"] += f" / 보조 추출({aux['field_label']}): {aux['sentence'][:160]}"
                notes.append({"field": "period_text", "note": f"신청기간 필드 비어 있음 — {aux['field_label']}에서 기한 문장을 찾아 "
                              f"'사유 발생 후 기한'으로 분류(절대 날짜는 만들지 않음): {aux['sentence'][:160]}"})
        if pr.needs_period_check:
            need += ["apply_start", "apply_end"]
            ev["apply_end"] = f"신청기간 유형: {pr.kind}" + (f" ({pr.reason})" if pr.reason else "")
        if pr.kind == R.KIND_ALWAYS:
            notes.append({"field": "period_text", "note": "상시 표기 — 실제 접수 가능 여부는 원문 확인"})

        # ---- 연령: 원문 표현 우선. API 수치는 의미(만 나이 여부)를 확인하지 않았으므로 그대로 표시
        def _int(v):
            try:
                return int(str(v).strip()) if v is not None and str(v).strip() != "" else None
            except ValueError:
                return None
        lo, hi = _int(row.get("sprtTrgtMinAge")), _int(row.get("sprtTrgtMaxAge"))
        code_range = (lo, hi) if lo is not None and hi is not None and (lo > 0 or hi > 0) else None
        af = R.age_fields(" ".join(x for x in (expl, g("addAplyQlfcCndCn")) if x), code_range, "온통청년 연령 값")
        if af["evidence"]:
            ev["age_condition"] = af["evidence"]
        notes += [{"field": "age_condition", "note": x} for x in af["notes"]]
        if g("sprtTrgtAgeLmtYn"):
            notes.append({"field": "age_condition", "note": f"API 연령제한여부 값 {g('sprtTrgtAgeLmtYn')} (값의 의미는 공식 명세로 확인 전)"})
        if af["need"]:
            need.append("age_condition")
            conflicts.append(af["conflict"])

        region, reg_basis = T.region_from_codes(g("zipCd"))
        if not region:
            need.append("region")

        income = g("earnEtcCn")
        if not income and (g("earnMinAmt") or g("earnMaxAmt")):
            need.append("income_condition")
            ev["income_condition"] = f"API 수치 earnMinAmt={g('earnMinAmt')}, earnMaxAmt={g('earnMaxAmt')} (단위 미확인)"

        stype, st_ev = T.support_type(name, content)
        if cat == "장학금" and dec.aid_kind:
            # 장학금 분야 안에서 대출·이자지원·채무조정을 무상 장학금과 구분
            stype, st_ev = YR.AID_SUPPORT_TYPE[dec.aid_kind], f"지원 유형 {dec.aid_kind}"
            aid_note = {YR.AID_LOAN: "학자금대출 — 새 대출 상품(상환 의무 있음), 무상 장학금이 아님",
                        YR.AID_INTEREST: "이자지원 — 대출 이자를 지원, 무상 장학금이 아님",
                        YR.AID_RECOVERY: "신용회복·채무조정 — 연체·채무 해소 지원, 무상 장학금이 아님",
                        YR.AID_REPAY: "대출 상환지원 — 이미 받은 대출의 상환액을 지원(새 대출·새 상환 의무 아님), 무상 장학금이 아님"}
            if dec.aid_kind in aid_note:
                notes.append({"field": "support_type", "note": aid_note[dec.aid_kind]})
        if not stype:
            need.append("support_type")
        elif st_ev:
            ev["support_type"] = st_ev
        am = R.amount_fields(content)
        if am["evidence"]:
            ev["amount_text"] = am["evidence"]
        notes += [{"field": "amount_text", "note": x} for x in am["notes"]]
        if am["need"]:
            need.append("amount_text")

        # ---- 링크: 응답에 있는 값만. 온통청년 상세 주소는 공식 규칙을 확인하지 못해 만들지 않음
        links, bad = [], []
        for fld, label in LINK_FIELDS:
            v = g(fld)
            if not v:
                continue
            if re.match(r"https?://\S+$", v):
                links.append({"type": label, "field": fld, "url": v})
            else:
                bad.append({"type": label, "field": fld, "text": v[:200]})
        apply_url = next((x["url"] for x in links if x["field"] == "aplyUrlAddr"), None)
        flags = {"links": links}
        if aux:
            flags["period_aux"] = aux
        if cat == "장학금" and dec.aid_kind:
            flags["aid_kind"] = dec.aid_kind
        if bad:
            flags["link_texts"] = bad
            notes.append({"field": "links", "note": "주소 형식이 아닌 링크 값 " + ", ".join(f"{x['type']} '{x['text'][:60]}'" for x in bad)})
        if not links:
            need.append(LINK_NEED)
            flags["link_missing"] = True

        extra = {}
        if cat in ("생활비", "주거비"):
            extra = {"amount_period_text": am["amount_text"], "budget_end": None, "household": None,
                     "housing": None, "self_pay": None, "loan_terms": None}
        elif cat == "장학금":
            extra = {"pay_amount": am["amount_text"], "income_cond": income}

        org = g("sprvsnInstCdNm", "operInstCdNm", "rgtrInstCdNm")
        fields = {
            "category": cat, "title": name, "organizer": org,
            "info_type": "상시 제도 안내" if pr.kind in (R.KIND_ALWAYS, R.KIND_NO_APPLY) else "모집공고",
            "target_text": _clip(expl), "youth_relevant": True,
            "youth_basis": "온통청년(청년정책 통합플랫폼) 등록 청년정책",
            "age_condition": af["age_condition"], "region": region, "region_condition": reg_basis,
            "income_condition": income, "status_condition": _clip(g("addAplyQlfcCndCn")),
            "benefit_text": _clip(content), "support_type": stype,
            "amount_text": am["amount_text"], "amount_value": am["amount_value"], "amount_unit": am["amount_unit"],
            "amount_period": am["amount_period"], "amount_qualifier": am["amount_qualifier"],
            "amount_items": am["amount_items"],
            "apply_start": pr.start, "apply_end": pr.end, "apply_end_time": pr.end_time,
            "period_kind": pr.kind, "period_flags": pr.flags, "period_month_end": pr.month_end,
            "period_closed_year": pr.closed_year, "review_notes": notes,
            "period_text": aply or (f"[{aux['field_label']}에서 보조 추출] {aux['sentence']}" if aux else None), "apply_method": _clip(g("plcyAplyMthdCn")),
            "apply_url": apply_url, "required_docs": _clip(g("sbmsnDcmntCn")),
            "exclusions": _clip(g("ptcpPrpTrgtCn")), "official_url": None,
            "attachment_url": None, "posted_at": T.ymd_compact(g("frstRegDt")), "extra": extra,
        }
        site_cat = " > ".join(x for x in (g("lclsfNm"), g("mclsfNm")) if x) or None
        raw = {"list": _raw(row)}
        return Item("youthcenter", "온통청년", pid, None, site_cat, fields, ev, need, source_digest=_digest(raw),
                    conflicts=conflicts, raw=raw, digest_version=DIGEST_VERSION, flags=flags)


# 신청기간 필드가 비었을 때만 보는 기한 문장. 기한 표현 바로 뒤의 동사로 신청·접수 기한과 심사·지급 처리기한을 구분한다
_AUX_DEADLINE = re.compile(r"(?:(?:로|으로)\s*부터\s*\d+\s*(?:일|개월|년)(?:이\s*되는\s*날)?|속한\s*달\s*말일|\d+\s*(?:일|개월|년)\s*이내)"
                           r"[^.。\n※○]{0,12}?(신청|접수|제출|지급|처리|통보|결정|심사|입금|송금|교부)")
_APPLY_VERBS = {"신청", "접수", "제출"}
_AUX_FIELDS = (("plcySprtCn", "지원내용"), ("plcyAplyMthdCn", "신청방법"))


def _sentence_at(t: str, start: int, end: int) -> str:
    a = max(t.rfind(ch, 0, start) for ch in ".。\n※○") + 1
    ends = [i for i in (t.find(ch, end) for ch in ".。\n※○") if i != -1]
    return T.clean(t[a:min(ends) if ends else len(t)])


def _aux_deadline(vals: dict) -> dict | None:
    """신청·접수 기한 문장만 돌려준다. '접수 후 30일 이내 지급' 같은 처리기한은 신청기한으로 쓰지 않는다."""
    for fld, label in _AUX_FIELDS:
        t = T.clean(vals.get(fld))
        if not t:
            continue
        for m in _AUX_DEADLINE.finditer(t):
            if m.group(1) not in _APPLY_VERBS:
                continue
            sent = _sentence_at(t, m.start(), m.end())
            if R.classify_period(sent).kind == R.KIND_EVENT or R.classify_period(m.group(0)).kind == R.KIND_EVENT:
                return {"field": fld, "field_label": label, "sentence": sent, "kind": R.KIND_EVENT, "verb": m.group(1)}
    return None


LINK_FIELDS = (("aplyUrlAddr", "신청 링크"), ("refUrlAddr1", "참고 링크 1"), ("refUrlAddr2", "참고 링크 2"))
LINK_NEED = "공식 안내 링크 확인 필요"
DIGEST_VERSION = 1
# 이름상 조회수로 보이는 값. 매번 바뀌어 원문 변경으로 오인하지 않도록 원본 요약값에서만 뺀다(확인 전 추정, 시험 보고서에 존재 여부 표시)
VOLATILE = {"inqCnt"}


def _raw(row: dict) -> dict:
    return {k: v for k, v in (row or {}).items() if k not in VOLATILE}


# 같은 정책인데 호출마다 값의 순서만 바뀌는 쉼표 목록(2026-10-16·18 응답 비교에서 키워드 474건이 순서만 달랐음).
# 원본은 그대로 보관하고, 원본 요약값(변경 감지)을 계산할 때만 순서를 맞춘다.
ORDERLESS = ("plcyKywdNm",)


def _digest(raw: dict) -> str:
    import copy
    import hashlib
    import json
    r = copy.deepcopy(raw)
    for sec in r.values():
        if isinstance(sec, dict):
            for k in ORDERLESS:
                if isinstance(sec.get(k), str) and "," in sec[k]:
                    sec[k] = ",".join(sorted(x.strip() for x in sec[k].split(",")))
    return hashlib.sha256(json.dumps(r, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:20]
