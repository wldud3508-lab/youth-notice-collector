"""분류 규칙(출처 공통). 원문 사실은 바꾸지 않고, 원문에서 확인되는 것만 구조화한다.

- 날짜·신청 상태를 추측해서 만들지 않는다.
- '상시'는 원문 표기일 뿐 실제 접수 가능을 보장하지 않는다.
- '필수 확인'(needs_review_fields)과 '참고사항'(review_notes)을 구분한다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import textutil as T

# ------------------------------------------------------------------ 신청기간 유형
KIND_DATED = "기간 명시"
KIND_ALWAYS = "상시"
KIND_NO_APPLY = "신청 절차 없음"
KIND_CLOSED = "원문에 종료 명시"
KIND_CLOSED_REL = "종료 표현(작성 연도 미확인)"
KIND_EVENT = "사유 발생 후 기한"
KIND_BY_ORG = "기관·유형별 일정 상이"
KIND_MONTH = "연도 있는 월 단위"
KIND_NOTICE = "별도 공고 확인"
KIND_RECUR = "매년·정기 반복"
KIND_BUDGET = "예산 소진 등 조건부"
KIND_MULTI = "신청기간 여러 개"
KIND_BAD = "원문 날짜 오류"
KIND_NONE = "원문 기간 미기재"
KIND_UNKNOWN = "분류 안 됨"

PERIOD_KINDS = [KIND_DATED, KIND_ALWAYS, KIND_NO_APPLY, KIND_CLOSED, KIND_CLOSED_REL, KIND_EVENT, KIND_BY_ORG,
                KIND_MONTH, KIND_NOTICE, KIND_RECUR, KIND_BUDGET, KIND_MULTI, KIND_BAD, KIND_NONE, KIND_UNKNOWN]
# 이 유형들은 실제 신청기간을 여전히 확인해야 한다(사유는 분류됐어도)
NEEDS_PERIOD_CHECK = {KIND_CLOSED_REL, KIND_EVENT, KIND_BY_ORG, KIND_MONTH, KIND_NOTICE, KIND_RECUR,
                      KIND_BUDGET, KIND_MULTI, KIND_BAD, KIND_NONE, KIND_UNKNOWN}

FLAG_BUDGET = "예산 소진 시 종료 가능"
FLAG_PLANNED = "예정 표기"
FLAG_MIXED = "여러 일정 혼재"

_CUE_EVENT = r"이내|부터\s*\d+\s*(?:일|개월|년)|발생\s*시|발생시|입영|전입\s*신고|혼인\s*신고|보호\s*종료|납입일|고용한\s*날|경과\s*후|통지서\s*수령"
_CUE_BYORG = r"상이|별로\s*다|에\s*따라\s*다|기관\s*별"
_CUE_NOTICE = r"공고|공지|홈페이지|누리집|별도|추후|안내|문의|www\.|규정에\s*따름|계획에\s*따"
_CUE_RECUR = r"매년|매월|매\s*학기|학기\s*(?:시작|초|별)|연\s*\d\s*회|분기|반기|상반기|하반기|연초|\d{1,2}\s*월|정기|매해"
_CUE_BUDGET = r"예산|소진|조기\s*(?:마감|종료)|선착순"


@dataclass
class PeriodResult:
    start: str | None = None
    end: str | None = None
    end_time: str | None = None
    kind: str = KIND_UNKNOWN
    flags: list[str] = field(default_factory=list)
    month_end: str | None = None      # 'YYYY-MM' — 원문에 연도와 끝 달이 명시된 경우만
    closed_year: int | None = None    # 원문에 연도와 함께 종료가 명시된 경우만
    reason: str | None = None

    @property
    def needs_period_check(self) -> bool:
        return self.kind in NEEDS_PERIOD_CHECK


def _month_end(t: str) -> str | None:
    """원문에 적힌 마지막 달(연도 포함). 일자는 만들지 않는다.
    '2026.7~8월중' → 2026-08, '2025. 8. ~ 2026. 2.' → 2026-02, '2026년 1월(상반기), 7월(하반기)' → 2026-07.
    연도가 앞에 없거나 끝이 열려 있으면(~예산 소진) None."""
    tokens = []   # (위치, 연도 또는 None, 월)
    for m in re.finditer(r"(20\d{2})\s*[.년\-]\s*(\d{1,2})(?!\d)", t):
        tokens.append((m.start(), m.end(), int(m.group(1)), int(m.group(2))))
    covered = [(a, b) for a, b, _, _ in tokens]
    for m in re.finditer(r"(?<!\d)(\d{1,2})\s*월", t):
        if not any(a <= m.start() < b for a, b in covered):
            tokens.append((m.start(), m.end(), None, int(m.group(1))))
    tokens.sort()
    y = prev = None
    end = None
    for _, _, ty, tm in tokens:
        if not 1 <= tm <= 12:
            return None
        if ty is not None:
            y = ty
        elif y is None:
            continue                      # 연도가 나오기 전의 달은 연도를 알 수 없음
        elif prev is not None and tm < prev:
            y += 1                        # '11월~2월' 처럼 해를 넘김
        prev, end = tm, (y, tm)
    if end is None:
        return None
    last_pos = tokens[-1][1]
    if re.match(r"\s*월?\s*(?:말|초|중순|중)?\s*\.?\s*[~∼\-]\s*(?:[^\d\s]|$)", t[last_pos:]):
        return None   # '2026년 2월 ~ 예산 소진 시까지', '2026. 4월~' 처럼 끝이 정해지지 않음
    return f"{end[0]:04d}-{end[1]:02d}"


def classify_period(text: str | None) -> PeriodResult:
    t = T.clean(text)
    r = PeriodResult()
    if not t:
        r.kind = KIND_NONE
        return r
    if re.search(_CUE_BUDGET, t):
        r.flags.append(FLAG_BUDGET)
    if "예정" in t:
        r.flags.append(FLAG_PLANNED)

    p = T.parse_period(t)
    if p.start or p.end:
        r.start, r.end, r.end_time, r.kind = p.start, p.end, p.end_time, KIND_DATED
        return r
    r.reason = p.reason
    if p.reason and "여러" in p.reason:
        r.kind = KIND_MULTI
        return r
    if p.reason and "오류" in p.reason:
        r.kind = KIND_BAD
        return r

    if re.search(r"신청\s*절차가?\s*없|신청\s*불필요|별도\s*신청\s*없", t):
        r.kind = KIND_NO_APPLY
        return r
    m = re.search(r"(20\d{2})\s*년?\s*(?:은|는|도|에는)?\s*.{0,15}?(미\s*모집|모집\s*(?:하지\s*않|없|안\s*함)|(?:접수|모집)\s*마감|(?:사업|모집)\s*종료)", t)
    if m:
        r.kind, r.closed_year = KIND_CLOSED, int(m.group(1))
        return r
    if re.search(r"(?:금년|올해|당해\s*연도|이번\s*연도)\s*.{0,6}(?:접수|모집)?\s*(?:마감|종료)|미\s*모집", t):
        r.kind = KIND_CLOSED_REL   # 원문 작성 연도를 알 수 없으므로 올해 종료로 단정하지 않음
        return r
    # 상시: 문장이 상시/연중으로 시작하거나, 다른 일정 단서 없이 상시만 있는 경우
    if re.match(r"^[\s\-·*]*(?:상시|연중)", t) or (re.search(r"상시|연중", t) and not re.search(f"{_CUE_BYORG}|{_CUE_NOTICE}|{_CUE_EVENT}", t)):
        r.kind = KIND_ALWAYS
        if re.search(f"{_CUE_BYORG}|{_CUE_NOTICE}|/", t):
            r.flags.append(FLAG_MIXED)
        return r
    if re.search(_CUE_EVENT, t):
        r.kind = KIND_EVENT
        return r
    if re.search(_CUE_BYORG, t):
        r.kind = KIND_BY_ORG
        return r
    if re.search(r"20\d{2}\s*[.년\-]\s*\d{1,2}(?!\d)", t):
        r.kind = KIND_MONTH
        if FLAG_PLANNED not in r.flags:   # '예정' 일정은 지난 것으로 보지 않음
            r.month_end = _month_end(t)
        return r
    if re.search(_CUE_NOTICE, t):
        r.kind = KIND_NOTICE
        return r
    if re.search(_CUE_RECUR, t):
        r.kind = KIND_RECUR
        return r
    if FLAG_BUDGET in r.flags:
        r.kind = KIND_BUDGET
        return r
    r.kind = KIND_UNKNOWN
    return r


# ------------------------------------------------------------------ 신청 상태 (기준일 계산)
ST_OPEN, ST_UPCOMING, ST_CLOSED = "접수중", "신청예정", "마감"
ST_ALWAYS, ST_ELAPSED, ST_CLOSED_TEXT, ST_UNCLEAR = "상시", "표기된 일정 경과(월 단위)", "종료 명시(원문)", "기간 확인 필요"
ST_NO_APPLY = "신청 절차 없음(원문)"
STATUS_ORDER = [ST_OPEN, ST_UPCOMING, ST_ALWAYS, ST_NO_APPLY, ST_UNCLEAR, ST_ELAPSED, ST_CLOSED_TEXT, ST_CLOSED]


def apply_status(n: dict, today: str) -> str:
    if n.get("apply_status_override"):
        return n["apply_status_override"]
    s, e = n.get("apply_start"), n.get("apply_end")
    if s or e:
        if s and today < s:
            return ST_UPCOMING
        if e and today > e:
            return ST_CLOSED
        return ST_OPEN
    kind = n.get("period_kind")
    if kind == KIND_ALWAYS or (kind is None and n.get("info_type") == "상시 제도 안내"):
        return ST_ALWAYS
    if kind == KIND_NO_APPLY:
        return ST_NO_APPLY
    if kind == KIND_CLOSED and n.get("period_closed_year") and n["period_closed_year"] >= int(today[:4]):
        return ST_CLOSED_TEXT
    me = n.get("period_month_end")
    if kind == KIND_MONTH and me and me < today[:7]:
        return ST_ELAPSED
    return ST_UNCLEAR


# ------------------------------------------------------------------ 연령
_AGE_RANGE = re.compile(r"(만\s*)?(\d{1,2})\s*세?\s*(?:이상)?\s*(?:~|∼|-|부터)\s*(만\s*)?(\d{1,2})\s*세\s*(?:이하|미만|까지)?")
_AGE_MAX = re.compile(r"(만\s*)?(\d{1,2})\s*세\s*(이하|미만)")
NOTE_AGE_BASIS = "나이 산정 기준(만 나이 여부) 미기재"


def parse_ages(text: str | None) -> list[dict]:
    """원문의 연령 표현을 모두 찾는다. 만 나이로 환산하지 않는다."""
    t = T.clean(text) or ""
    out, used = [], []
    for m in _AGE_RANGE.finditer(t):
        man = bool(m.group(1) or m.group(3))
        out.append({"lo": int(m.group(2)), "hi": int(m.group(4)), "man": man, "text": T.clean(m.group(0)),
                    "evidence": T.excerpt(t, m.start(), m.end())})
        used.append((m.start(), m.end()))
    for m in _AGE_MAX.finditer(t):
        if any(a <= m.start() < b for a, b in used):
            continue
        out.append({"lo": None, "hi": int(m.group(2)), "man": bool(m.group(1)), "text": T.clean(m.group(0)),
                    "evidence": T.excerpt(t, m.start(), m.end())})
    return out


def age_fields(text: str | None, code_range: tuple[int, int] | None, code_label: str = "지원조건 코드") -> dict:
    """age_condition, 근거, 참고사항, 필수 확인, 충돌을 돌려준다."""
    ages = parse_ages(text)
    res = {"age_condition": None, "evidence": None, "notes": [], "need": False, "conflict": None}
    if ages:
        distinct = list(dict.fromkeys(a["text"] for a in ages))
        res["age_condition"] = " / ".join(distinct)          # 원문 표현 그대로, 환산 없음
        res["evidence"] = ages[0]["evidence"]
        if not all(a["man"] for a in ages):
            res["notes"].append(NOTE_AGE_BASIS)
        if len({(a["lo"], a["hi"]) for a in ages}) > 1:
            res["notes"].append("연령 조건 여러 개(대상별 조건일 수 있음)")
        if code_range and len(ages) == 1 and ages[0]["lo"] is not None and (ages[0]["lo"], ages[0]["hi"]) != code_range:
            res["need"] = True
            res["conflict"] = {"field": "age_condition", "values": [
                {"site": "원문", "value": ages[0]["text"]},
                {"site": code_label, "value": f"{code_range[0]}~{code_range[1]}세"}]}
    elif code_range:
        res["age_condition"] = f"{code_range[0]}~{code_range[1]}세 ({code_label})"
        res["evidence"] = f"{code_label}: {code_range[0]}~{code_range[1]}"
        res["notes"].append(NOTE_AGE_BASIS)
    return res


# ------------------------------------------------------------------ 금액
_AMT = T._AMT


def parse_amounts(text: str | None) -> list[dict]:
    """금액 표현을 모두 원문 그대로 항목별로 정리한다(대표 금액으로 합치지 않음)."""
    t = T.clean(text) or ""
    items = []
    prev_end = 0
    for m in _AMT.finditer(t):
        a = T.parse_amount(m.group(0))
        clause_start = max([t.rfind(ch, 0, m.start()) + 1 for ch in ",;/\n·※()[]"] + [prev_end])
        prev_end = m.end()
        label = T.clean(t[clause_start:m.start()])
        if label:
            label = label[-20:].strip(" :-~∼")
            if re.fullmatch(r"(?:또는|및|혹은|내지|와|과|부터|에서|~|\s)*", label):
                label = ""   # 연결어만 있으면 구분 설명이 아님
        items.append({"text": a.text, "value": a.value, "unit": a.unit, "period": a.period,
                      "qualifier": a.qualifier, "label": label or None,
                      "evidence": T.excerpt(t, m.start(), m.end(), 50)})
    return items


def amount_fields(text: str | None) -> dict:
    items = parse_amounts(text)
    res = {"amount_text": None, "amount_value": None, "amount_unit": None, "amount_period": None,
           "amount_qualifier": None, "amount_items": items, "evidence": None, "notes": [], "need": False}
    if not items:
        return res
    distinct = list(dict.fromkeys(f"{i['label']} {i['text']}" if i["label"] else i["text"] for i in items))
    res["evidence"] = items[0]["evidence"]
    if len(distinct) == 1:
        i = items[0]
        res.update(amount_text=i["text"], amount_value=i["value"], amount_unit=i["unit"],
                   amount_period=i["period"], amount_qualifier=i["qualifier"])
        return res
    res["amount_text"] = " · ".join(distinct[:3]) + (f" 외 {len(distinct) - 3}개" if len(distinct) > 3 else "")
    res["notes"].append(f"금액 {len(distinct)}개 — 항목별로 정리(대표 금액으로 합치지 않음)")
    # 단위·한정어·설명이 모두 같은데 값이 다르면 서로 구분되지 않음(의미 불명확·충돌) → 필수 확인
    seen: dict = {}
    for i in items:
        key = (i["period"], i["qualifier"], i["label"])
        if key in seen and seen[key] != i["value"]:
            res["need"] = True
            res["notes"].append("구분되지 않는 서로 다른 금액이 있음")
            break
        seen.setdefault(key, i["value"])
    return res
