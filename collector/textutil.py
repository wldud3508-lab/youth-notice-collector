"""규칙 기반 추출 유틸리티 (AI 요약 없음).

원칙
- 원문에서 찾은 표현은 그대로 보존한다. '월 최대 20만 원'을 '20만 원'으로 바꾸지 않는다.
- 확실하지 않으면 값을 비우고(None) needs_review에 필드명을 넣는다.
- 게시일을 신청 시작일로 쓰지 않는다. 날짜는 신청기간 원문에서만 뽑는다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

# ---------------------------------------------------------------- 공통

def clean(s) -> str | None:
    """공백 정리. 빈 값·공백뿐인 값은 None."""
    if s is None:
        return None
    s = str(s).replace("\r", "").strip()
    s = re.sub(r"[ \t ]+", " ", s)
    return s or None


def norm_key(s: str | None) -> str:
    """비교용 정규화(공백·괄호·특수문자 제거, 소문자)."""
    if not s:
        return ""
    s = re.sub(r"\[.*?\]|\(.*?\)", "", s)
    return re.sub(r"[^0-9a-zA-Z가-힣]", "", s).lower()


def excerpt(text: str | None, start: int, end: int, width: int = 60) -> str:
    """근거 발췌: 매칭 위치 주변만 짧게 자른다(원문 전체 복제 방지)."""
    if not text:
        return ""
    a = max(0, start - width // 2)
    b = min(len(text), end + width // 2)
    s = text[a:b].replace("\n", " ").strip()
    return ("…" if a > 0 else "") + s + ("…" if b < len(text) else "")


# ---------------------------------------------------------------- 날짜

_YMD = r"(\d{4})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})\s*\.?\s*일?"
_MD = r"(\d{1,2})\s*[.\-/월]\s*(\d{1,2})\s*\.?\s*일?"
_COMPACT = r"(?<!\d)(\d{4})(\d{2})(\d{2})(?!\d)"
_TIME = r"(\d{1,2})\s*:\s*(\d{2})"


def _d(y, m, d) -> str | None:
    try:
        return date(int(y), int(m), int(d)).isoformat()
    except ValueError:
        return None


@dataclass
class Period:
    start: str | None = None
    end: str | None = None
    end_time: str | None = None
    always: bool = False          # 상시
    needs_review: bool = False    # 원문이 복잡하거나 해석 불가
    reason: str | None = None


def parse_period(text: str | None) -> Period:
    """신청기간 원문 → 시작일/마감일/마감시각.

    지원 형식: '2026. 2. 26. ~ 2026. 10. 15.', '2026-03-03 ~ 03-18', '20260101 ~ 20261231',
    '~ 2026. 3. 18. 18:00', '상시', '상시신청'. 범위가 두 개 이상이면 확정하지 않고 검토로 넘긴다.
    """
    p = Period()
    t = clean(text)
    if not t:
        p.needs_review, p.reason = True, "신청기간 원문 없음"
        return p
    t = normalize_years(t)
    if re.search(r"상시|연중", t) and not re.search(r"\d{4}", t):
        p.always = True   # 상시 표기일 뿐 실제 접수 가능 보장은 아님. 세부 판정은 rules.classify_period
        return p

    ranges = re.split(r"\n|(?<=\))\s*,", t)
    found = []
    for chunk in ranges:
        dates: list[str] = []
        for m in re.finditer(_YMD, chunk):
            d = _d(*m.groups())
            if d:
                dates.append(d)
        if not dates:
            for m in re.finditer(_COMPACT, chunk):
                d = _d(*m.groups())
                if d:
                    dates.append(d)
        # '2026. 10. 5. ~ 10. 24.' 처럼 끝 날짜에 연도가 없는 경우
        if len(dates) == 1 and "~" in chunk:
            left, right = chunk.split("~", 1)
            if re.search(_YMD, left) and not re.search(_YMD, right):
                m = re.search(_MD, right)
                if m:
                    y = int(dates[0][:4])
                    d = _d(y, *m.groups())
                    if d and d < dates[0]:
                        d = _d(y + 1, *m.groups())
                    if d:
                        dates.append(d)
            elif not re.search(_YMD, left) and re.search(_YMD, right):
                dates = [None, dates[0]]  # '~ 2026. 3. 18.' 마감만
        if dates:
            found.append((chunk, dates))

    if not found:
        p.needs_review, p.reason = True, "날짜 형식을 해석하지 못함"
        return p
    if len(found) > 1:
        p.needs_review, p.reason = True, "신청기간이 여러 개(정기·추가 등)"
        return p

    chunk, dates = found[0]
    if len(dates) >= 2:
        p.start, p.end = dates[0], dates[-1]
    elif "~" in chunk and chunk.strip().startswith("~"):
        p.end = dates[0]
    else:
        # 날짜 하나만 있음: 시작인지 마감인지 원문 단서로만 판단
        if re.search(r"까지|마감", chunk):
            p.end = dates[0]
        elif re.search(r"부터", chunk):
            p.start = dates[0]
        else:
            p.needs_review, p.reason = True, "날짜가 하나뿐이라 시작·마감 구분 불가"
            return p
    tm = list(re.finditer(_TIME, chunk))
    if tm and p.end:
        h, mi = tm[-1].groups()
        if 0 <= int(h) <= 24:
            p.end_time = f"{int(h):02d}:{mi}"
    if p.start and p.end and p.start > p.end:
        # 원문 오기 가능성. 어느 쪽이 맞는지 알 수 없으므로 날짜를 쓰지 않는다.
        p.start = p.end = p.end_time = None
        p.needs_review, p.reason = True, "원문 날짜 오류(시작일이 마감일보다 늦음)"
    return p


def normalize_years(t: str) -> str:
    """실제 원문에서 확인한 연도 표기 변형만 표준형으로 바꾼다(날짜를 새로 만들지 않음).
    "'26. 2. 9." → "2026. 2. 9.", "(2026년) 4. 20." → "2026. 4. 20.", "2026 4. 20." → "2026. 4. 20.\""""
    s = re.sub(r"['’‘](\d{2})(?=\s*[.년])", lambda m: "20" + m.group(1), t)
    s = re.sub(r"\(\s*(20\d{2})\s*년?\s*\)\s*", r"\1. ", s)
    s = re.sub(r"(?<![\d.])(20\d{2})\s+(\d{1,2})\s*\.", r"\1. \2.", s)
    return s


def ymd_compact(s: str | None) -> str | None:
    """'20260915103320' / '20260915' / '2026-09-15...' → '2026-09-15'"""
    s = clean(s)
    if not s:
        return None
    m = re.match(r"(\d{4})-?(\d{2})-?(\d{2})", s)
    return _d(*m.groups()) if m else None


# ---------------------------------------------------------------- 금액

_AMT = re.compile(
    r"(?P<pre>(?:월|연|년|매월|학기당|학기|1회|회당|1인당|인당|건당|총|최대|최고|연간|월간)\s*(?:최대|최고)?\s*)?"
    r"(?P<num>\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(?P<unit>억\s*원|천만\s*원|백만\s*원|만\s*원|천\s*원|원)"
)
_UNIT_MUL = {"억원": 100_000_000, "천만원": 10_000_000, "백만원": 1_000_000, "만원": 10_000, "천원": 1_000, "원": 1}


@dataclass
class Amount:
    text: str | None = None
    value: float | None = None
    unit: str | None = None
    period: str | None = None
    qualifier: str | None = None
    evidence: str | None = None
    ambiguous: bool = False


def parse_amount(text: str | None) -> Amount:
    """지원내용에서 첫 금액 표현을 원문 그대로 뽑는다. 금액이 여러 종류면 ambiguous."""
    a = Amount()
    t = clean(text)
    if not t:
        return a
    matches = list(_AMT.finditer(t))
    if not matches:
        return a
    m = matches[0]
    pre = (m.group("pre") or "").replace(" ", "")
    a.text = clean(m.group(0))
    unit = m.group("unit").replace(" ", "")
    try:
        a.value = float(m.group("num").replace(",", "")) * _UNIT_MUL[unit]
        if a.value.is_integer():
            a.value = int(a.value)
        a.unit = "원"
    except (KeyError, ValueError):
        a.value = None
    if re.search(r"월", pre):
        a.period = "월"
    elif re.search(r"연|년", pre):
        a.period = "연"
    elif "학기" in pre:
        a.period = "학기"
    elif re.search(r"1회|회당", pre):
        a.period = "1회"
    if re.search(r"최대|최고", pre):
        a.qualifier = "최대"
    elif "총" in pre:
        a.qualifier = "총액"
    elif re.search(r"건당|1인당|인당", pre):
        a.qualifier = "건당" if "건" in pre else "1인당"
    a.evidence = excerpt(t, m.start(), m.end())
    distinct = {mm.group(0).replace(" ", "") for mm in matches}
    a.ambiguous = len(distinct) > 1
    return a


# ---------------------------------------------------------------- 지원방식

def support_type(*texts: str | None) -> tuple[str | None, str | None]:
    """(지원방식, 근거). 확실한 단서가 없으면 (None, None)."""
    t = " ".join(x for x in texts if x)
    if not t:
        return None, None
    rules = [
        ("이자지원", r"이자\s*지원|이차\s*보전|이자\s*(?:일부|차액)|대출\s*이자"),
        ("대출", r"대출|융자|대부"),
        ("무상지원", r"지급|수당|지원금|장려금|바우처|환급|보조금|장학금|현금"),
    ]
    for name, pat in rules:
        m = re.search(pat, t)
        if m:
            return name, excerpt(t, m.start(), m.end())
    return None, None


# ---------------------------------------------------------------- 연령

_AGE_RANGE = re.compile(r"(만\s*)?(\d{2})\s*세?\s*(?:이상)?\s*(?:~|∼|-|부터)\s*(만\s*)?(\d{2})\s*세\s*(?:이하|미만|까지)?")
_AGE_MAX = re.compile(r"(만\s*)?(\d{2})\s*세\s*(이하|미만)")


def parse_age(text: str | None) -> tuple[str | None, str | None, bool]:
    """(연령 조건, 근거, 만 나이 확인 필요 여부)."""
    t = clean(text)
    if not t:
        return None, None, False
    m = _AGE_RANGE.search(t)
    if m:
        man = bool(m.group(1) or m.group(3))
        lo, hi = m.group(2), m.group(4)
        cond = f"만 {lo}~{hi}세" if man else f"{lo}~{hi}세 (만 나이 여부 원문 미표기)"
        return cond, excerpt(t, m.start(), m.end()), not man
    m = _AGE_MAX.search(t)
    if m:
        man = bool(m.group(1))
        cond = f"{'만 ' if man else ''}{m.group(2)}세 {m.group(3)}" + ("" if man else " (만 나이 여부 원문 미표기)")
        return cond, excerpt(t, m.start(), m.end()), not man
    return None, None, False


# ---------------------------------------------------------------- 청년 관련성

_YOUTH = re.compile(r"청년|대학생|대학\(원\)생|대학원생|재학생|휴학생|졸업\s*예정|사회초년생|신혼부부?\s*·?\s*청년|"
                    r"만\s*(1[5-9]|2\d|3\d)\s*세\s*(?:이상)?\s*(?:~|∼|-)\s*(?:만\s*)?(2\d|3\d)\s*세")


def youth_relevance(*texts: str | None) -> tuple[bool | None, str | None]:
    t = " ".join(x for x in texts if x)
    if not t:
        return None, None
    m = _YOUTH.search(t)
    if m:
        return True, f"원문에 '{m.group(0)}' 표현: {excerpt(t, m.start(), m.end(), 40)}"
    return False, "원문에서 청년·대학생 대상 표현을 찾지 못함"


# ---------------------------------------------------------------- 분야 분류

HOUSING = re.compile(r"월세|임차|임대료|전세|전월세|보증금|주거급여|주거비|이사비|중개\s*수수료|주거\s*안정|주택\s*구입|주거|행복주택|임대주택|입주자")
LIVING = re.compile(r"생활비|생활\s*안정|생활\s*지원|청년\s*수당|구직\s*활동\s*지원|수당|교통비|통신비|식비|지원금|장려금|바우처|저축|자산\s*형성|긴급\s*지원")
SCHOLAR = re.compile(r"장학|학자금|등록금")
PUBLIC_RENTAL = re.compile(r"(입주자|입주\s*대상자)\s*모집|행복주택|국민임대|영구임대|매입임대|전세임대\s*입주|공공임대|임대주택\s*공급")


def classify(name: str | None, *texts: str | None, include_public_rental: bool = False) -> tuple[str | None, str | None]:
    """프로그램 분류(생활비/주거비/장학금). 출처의 분류와 별도로 관리한다. (분야, 근거)"""
    title = name or ""
    body = " ".join(x for x in texts if x)
    if PUBLIC_RENTAL.search(title) and not include_public_rental:
        return None, "공공임대 입주자 모집(기본 수집 범위 제외)"
    for cat, pat in (("장학금", SCHOLAR), ("주거비", HOUSING), ("생활비", LIVING)):
        m = pat.search(title)
        if m:
            return cat, f"공고명에 '{m.group(0)}'"
    for cat, pat in (("장학금", SCHOLAR), ("주거비", HOUSING), ("생활비", LIVING)):
        m = pat.search(body)
        if m:
            return cat, f"본문에 '{m.group(0)}': {excerpt(body, m.start(), m.end(), 40)}"
    return None, None


# ---------------------------------------------------------------- 지역

SIDO = [
    ("서울", r"서울"), ("부산", r"부산"), ("대구", r"대구"), ("인천", r"인천"), ("광주", r"광주광역시|광주시(?!\s*(?:경기|도))"),
    ("대전", r"대전"), ("울산", r"울산"), ("세종", r"세종"), ("경기", r"경기"), ("강원", r"강원"),
    ("충북", r"충청북도|충북"), ("충남", r"충청남도|충남"), ("전북", r"전라북도|전북"), ("전남", r"전라남도|전남"),
    ("경북", r"경상북도|경북"), ("경남", r"경상남도|경남"), ("제주", r"제주"),
]
SIDO_CODE = {"11": "서울", "26": "부산", "27": "대구", "28": "인천", "29": "광주", "30": "대전", "31": "울산", "36": "세종",
             "41": "경기", "42": "강원", "51": "강원", "43": "충북", "44": "충남", "45": "전북", "52": "전북", "46": "전남",
             "47": "경북", "48": "경남", "50": "제주"}
CENTRAL = re.compile(r"부$|처$|청$|위원회$|공단|공사$|진흥원|재단|센터$|한국|국민|중앙")


def region_from_org(org: str | None) -> tuple[str | None, str | None]:
    """소관기관명 → 정규화 지역. 지자체면 시·도, 중앙부처·공공기관이면 '전국'. 모르면 None."""
    o = clean(org)
    if not o:
        return None, None
    for name, pat in SIDO:
        if re.match(rf"^\s*(?:{pat})", o):
            return name, f"소관기관 '{o}'"
    if CENTRAL.search(o):
        return "전국", f"소관기관 '{o}'(중앙부처·공공기관으로 판단)"
    return None, None


def region_from_codes(codes: str | None) -> tuple[str | None, str | None]:
    """온통청년 zipCd(쉼표 구분 법정동코드) → 지역."""
    c = clean(codes)
    if not c:
        return None, None
    sidos = sorted({SIDO_CODE.get(x.strip()[:2]) for x in c.split(",") if x.strip()[:2] in SIDO_CODE})
    if not sidos:
        return None, None
    if len(sidos) >= 17:
        return "전국", "지역코드가 전국 시·도를 포함"
    if len(sidos) == 1:
        return sidos[0], f"지역코드 {len(c.split(','))}개, 모두 {sidos[0]}"
    return "복수 지역", "지역코드 시·도: " + ", ".join(sidos)
