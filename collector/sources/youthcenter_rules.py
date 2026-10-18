"""온통청년 정책의 수집 범위 판정(포함 / 분류 검토 / 제외).

원칙
- '지원금', '등록금' 같은 단어 하나로 분야를 정하지 않는다. 정책명·지원목적(정책설명)·지원내용·수혜자를 함께 본다.
- '인턴', '출산', '의료' 같은 단어만으로 제외하지 않는다. 그런 정책은 '분류 검토'로 보낸다.
- 자동 제외는 (1) 강연·행사이면서 개인 지급 표현이 없는 경우, (2) 기업·기관에만 지급되는 지원,
  (3) 공공임대 입주자 모집(기본 범위 밖), (4) 장학금·생활비·주거비 단서가 전혀 없는 경우뿐이다.
- 판정은 '지원 목적·실제 수혜자·지원방식'이 서로 맞는지로 한다. 근거 문장에는 실제로 확인한 항목 범위만 적는다.
- 판정마다 근거(어느 항목의 어떤 표현)와 사유를 남긴다.
- 온통청년 분류값(대분류 lclsfNm·중분류 mclsfNm·키워드 plcyKywdNm)은 실제 값 목록을 확인하기 전이라
  포함 근거로 쓰지 않고, 근거 기록과 '분류 검토'로 보내는 데만 쓴다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .. import textutil as T

INCLUDE, REVIEW, EXCLUDE = "포함", "분류 검토", "제외"

# 제외 사유
EX_NO_ID = "정책번호·정책명 없음"
EX_RENTAL = "공공임대 입주자 모집(기본 범위 밖)"
EX_EVENT = "강연·행사(개인 지급 표현 없음)"
EX_EMPLOYER = "기업·기관 대상 지원(개인 지급 표현 없음)"
EX_NO_CUE = "장학금·생활비·주거비 단서 없음"
EX_BUSINESS = "사업화·기업·단체 운영·사업장 비용 지원(개인 생활비·주거비 아님)"
# 분류 검토 사유
RV_EVENT_MONEY = "강연·행사 형태이나 금액·지급 표현 있음"
RV_EMPLOYER_MIXED = "기업 지원과 개인 지급 표현이 함께 있음"
RV_MEDICAL_BIRTH = "주된 목적이 의료·임신·출산·육아 지원"
RV_JOB = "주된 목적이 취업·직무 준비"
RV_STARTUP = "주된 목적이 창업·사업"
RV_ASSET = "자산형성(저축·계좌 매칭)"
RV_INKIND = "현물·서비스 제공(현금·바우처 지급 확인 안 됨)"
RV_HOUSING_PROVIDE = "주거 공간 제공(비용 지원인지 확인 필요)"
RV_HOUSING_FIELD = "온통청년 분류가 주거이나 비용 지원 표현 없음"
RV_BODY_TUITION = "본문에만 등록금·학자금 언급"
RV_GENERIC_MONEY = "일반 지원 표현만 있음(생활비 성격·개인 지급 확인 필요)"
RV_CASH_ONLY = "금액 표기만 있음(지원 성격 확인 필요)"
RV_DEDUCTION = "소득공제·감면 등 제도 규칙(현금 지급 확인 안 됨)"
RV_HOUSING_SUPPLY = "주택 공급 정책 안내(개인 주거비 지원 신청인지 확인 필요)"
RV_BUSINESS = "사업 비용 지원과 개인 생활비 지급이 함께 있음(구분 필요)"
RV_EMPLOYER_HOUSING = "기업에 지급하는 근로자 주거 지원(개인 직접 지급인지 확인 필요)"
RV_COST_UNCLEAR = "임대료·보증금 등의 용도(주거용·사업장) 구분 안 됨"
RV_LOAN_INTEREST = "대출 이자 지원(대출 용도가 주거비·학자금인지 확인 필요)"

EXCLUDE_REASONS = [EX_NO_ID, EX_RENTAL, EX_EVENT, EX_EMPLOYER, EX_NO_CUE, EX_BUSINESS]
REVIEW_REASONS = [RV_EVENT_MONEY, RV_EMPLOYER_MIXED, RV_MEDICAL_BIRTH, RV_JOB, RV_STARTUP, RV_ASSET, RV_INKIND,
                  RV_HOUSING_PROVIDE, RV_HOUSING_FIELD, RV_BODY_TUITION, RV_GENERIC_MONEY, RV_CASH_ONLY,
                  RV_DEDUCTION, RV_HOUSING_SUPPLY, RV_BUSINESS, RV_EMPLOYER_HOUSING, RV_COST_UNCLEAR, RV_LOAN_INTEREST]

_MONEY = re.compile(r"\d[\d,.]*\s*(?:만|천|백|억)?\s*원|지급|수당|장려금|바우처|포인트|지역\s*화폐|상품권|현금|환급|"
                    r"이자\s*(?:지원|보전)|이차\s*보전|감면")
_EVENT = re.compile(r"콘서트|강연|특강|토크\s*쇼|축제|페스티벌|박람회|포럼|세미나|설명회|공연|토크\s*콘서트|캠프|워크[숍샵]")
_EMPLOYER = re.compile(r"(?:기업|사업주|사업장|고용주|업체|법인|소상공인)(?:이|가|에|에게|에\s*대해|당|별|은|는)?"
                       r"[^.。]{0,40}?(?:인건비|채용\s*장려금|고용\s*장려금|지원금|보조금|지원|지급)")
_LABOR_COST = re.compile(r"인건비|채용\s*장려금|고용\s*장려금|고용\s*유지")
_INDIVIDUAL = re.compile(r"청년(?:에게|\s*본인|\s*1인당|\s*개인(?:에게)?|\s*근로자에게|\s*계좌|에\s*(?:직접\s*)?지급)|1인당|개인별|"
                         r"본인\s*(?:계좌|명의)|(?:참여자|대상자|수혜자|참가자)(?:에게|\s*1인당)|청년\s*근속\s*장려금")

_SCH_TITLE = re.compile(r"장학|학자금|등록금")
_SCH_BODY = re.compile(r"장학금\s*(?:을|를)?\s*(?:지급|지원|수여)|장학생\s*(?:을|를)?\s*(?:선발|모집)")
_SCH_WEAK = re.compile(r"등록금|학자금")
_HOUS_COST = r"월세|전세|전월세|임차\s*보증금|보증금|임차료|임대료|주거비|이사비|중개\s*수수료|주거\s*급여|보증료|" \
             r"(?:주택|전세|전월세|주거|임차)\s*(?:자금\s*)?대출(?:\s*이자)?"
_HOUS_TITLE = re.compile(_HOUS_COST)
_HOUS_GIVE = re.compile(rf"(?:{_HOUS_COST})[^.。]{{0,15}}?(?:지원|지급|보전|감면|환급|대출|대여)")
_HOUS_PROVIDE = re.compile(r"쉐어\s*하우스|셰어\s*하우스|기숙사|임대\s*주택|행복\s*주택|공공\s*임대|매입\s*임대|입주|주택\s*공급|숙소|주거\s*공간")
_LIVING_STRONG = re.compile(r"생활비|생활\s*안정\s*(?:자금|지원금)|생계비|청년\s*수당|구직\s*(?:활동\s*)?(?:지원금|수당)|"
                            r"교통비|통신비|(?<!예)식비|생활\s*지원금|긴급\s*(?:생계|복지)|난방비|에너지\s*바우처")
_LIVING_GENERIC = re.compile(r"지원금|장려금|수당|바우처|지역\s*화폐|포인트|상품권|현금")
_FOOD_INKIND = re.compile(r"아침밥|아침\s*식사|식사|급식|도시락|식단|먹거리|반찬")
_ASSET = re.compile(r"자산\s*형성|저축|적금|통장|도약\s*계좌|내일\s*저축|희망\s*키움|계좌\s*(?:매칭|지원)")
_MED_BIRTH = re.compile(r"의료비|진료|검사비|치료|난임|건강\s*검진|병원|수술|임신|임산부|출산|출생|산후|산모|육아|보육|아이\s*돌봄|심리\s*상담|정신\s*건강")
_JOB = re.compile(r"인턴|일경험|일\s*경험|직무|채용|(?<!미)취업|직업\s*훈련|훈련|자격증|어학|면접")
_STARTUP = re.compile(r"창업|사업화|기업가(?!적)|사업\s*자금|점포|소상공인")
_DEDUCT = re.compile(r"(?:근로|사업|소득)\s*(?:·\s*사업)?\s*소득\s*공제|소득\s*공제|(?:근로|사업)\s*소득\s*공제|공제\s*(?:확대|적용|기준)|근로\s*인센티브|"
                     r"소득\s*산정\s*(?:시\s*)?제외|소득\s*인정액")
_SUPPLY = re.compile(r"(?:주택|임대\s*주택|공공\s*주택|안심\s*주택|역세권\s*청년주택)\s*(?:을|를)?\s*(?:공급|매입|건설|조성)|"
                     r"(?:공급|매입)\s*(?:형|임대)|청년\s*안심\s*주택|역세권\s*청년\s*주택|입주자\s*모집")
# 장학금 분야 안의 지원 유형(대출을 무상 장학금으로 보이게 하지 않음)
_AID_RECOVERY = re.compile(r"신용\s*회복|채무\s*조정|연체|분할\s*상환\s*(?:약정|지원)|신용\s*유의")
_AID_INTEREST = re.compile(r"이자\s*(?:지원|감면|면제|보전)|이차\s*보전|무이자|대출\s*이자|발생\s*이자")
# 이미 받은 대출의 상환액을 대신 지원(새 대출 아님, 새 상환 의무 없음)
_AID_REPAY = re.compile(r"상환\s*(?:액|금)?\s*(?:의\s*)?(?:일부\s*|전액\s*)?(?:지원|보조|대납)|상환\s*지원|대신\s*상환")
_AID_LOAN = re.compile(r"대출|융자")
AID_SCHOLAR, AID_LOAN, AID_INTEREST, AID_RECOVERY, AID_REPAY = "장학금", "학자금대출", "이자지원", "신용회복·채무조정", "대출 상환지원"
AID_SUPPORT_TYPE = {AID_SCHOLAR: "무상지원", AID_LOAN: "대출", AID_INTEREST: "이자지원", AID_RECOVERY: "기타",
                    AID_REPAY: "무상지원"}
_CASH = re.compile(r"\d[\d,.]*\s*(?:만|천|백|억)?\s*원")

# ---- '창업'이 실제로 무엇을 지원하는지 구분하기 위한 표현
# 취·창업 병기, (예비)창업 병기 같은 부수 언급은 창업 정책 단서에서 뺀다
_STARTUP_INCIDENTAL = re.compile(r"(?:취|구직|취업)\s*[·‧ㆍ,/]?\s*(?:또는|및|이나|과|와)?\s*(?:\(\s*예비\s*\)\s*)?창업\s*(?:\(\s*희망\s*\)\s*)?")
# 지원 비용이 사업 비용인 경우(사업화 자금·운영비·사업장 임대료·기업 보증료)
_BIZ_FUND = re.compile(r"사업화\s*(?:자금|지원금|지원|비)|창업\s*(?:자금|지원금)|사업\s*자금|운영\s*(?:자금|비)|경영\s*(?:자금|활동)|"
                       r"기업당|업체당|개소당|팀당|마케팅\s*비")
_BIZ_PLACE = r"사업장|점포|사무실|매장|상가|공장|작업장|창업\s*공간|창작\s*공간|업무\s*공간|공유\s*오피스|농지|축사|재배\s*시설|영농|" \
             r"창업\s*기업|청년\s*기업|기업의|소상공인|업체"
_COST_WORD = re.compile(r"월세|전세|전월세|임차\s*보증금|보증금|임차료|임대료|주거비|이사\s*비용?|이주\s*비용|중개\s*수수료|주거\s*급여|"
                        r"보증\s*(?:수수료|료)|주거\s*지원비|주택\s*보조금|(?:주택|전세|전월세|주거|임차|보증금)\s*(?:구입\s*|마련\s*)?(?:자금\s*)?(?:을|를)?\s*대출\s*(?:이자)?|"
                        r"주택\s*(?:구입|마련)\s*자금|대출\s*이자")
_GIVE_AFTER = re.compile(r"^[^.。\n]{0,18}?(지원|지급|보전|감면|환급|대출|대여|면제)")
_PLAN_AFTER = re.compile(r"^\s*(?:지원|지급)?\s*(?:계획|방안|정책\s*수립|체계)")
_NEG_AFTER = re.compile(r"^[^.。\n]{0,30}?(?:사용\s*불가|지원\s*(?:불가|제외)|은\s*제외|는\s*제외|등\s*제외)")
_TITLE_GIVE = re.compile(r"지원|지급|대출|감면|보조|환급")
# 근로·활동의 대가(임금·강사료·근무수당)는 생활비 지원으로 보지 않는다
_WAGE = re.compile(r"(?:강사|근무|지역\s*근무|야간|당직)\s*수당|강사료|인건비|임금|급여\s*(?:지급|수준|기준)")
_ASSET_METHOD = re.compile(r"적립\s*(?:후|하여)\s*만기|매칭\s*(?:적립|지원)|만기\s*(?:시|에)\s*[^.。]{0,15}(?:지급|수령)|본인\s*저축")
_TO_EMPLOYER = re.compile(r"(?:기업|사업주|업체|사업장)\s*(?:에\s*대해|에게|에\s*대하여)\s*[^.。]{0,30}?(?:지원|지급)")
_LIVING_PURPOSE = re.compile(r"자립|정착|생활\s*안정|생계|생활\s*(?:비용|부담)")
_PERSONAL_PAY = re.compile(r"1인\s*(?:당)?\s*(?:최대\s*|월\s*|연\s*)*\d|(?:본인|대상자|퇴원\s*대상자|신청인|수급자)\s*(?:명의\s*)?계좌|"
                           r"(?:자|생|인|년|동|부부|청년)\s*에게\s*[^.。]{0,25}?(?:지급|지원)")
# 신청기한(신청·접수) 과 처리기한(심사·지급) 구분은 youthcenter._aux_deadline 에서


@dataclass
class Decision:
    verdict: str                       # 포함 / 분류 검토 / 제외
    category: str | None = None        # 포함: 분야, 분류 검토: 잠정 분야(없을 수 있음)
    reason: str = ""                   # 제외·분류 검토 사유(포함이면 빈 문자열)
    basis: list[str] = field(default_factory=list)   # 판단 근거
    aid_kind: str | None = None        # 장학금 분야일 때: 장학금 / 학자금대출 / 이자지원 / 신용회복·채무조정

    def basis_text(self) -> str:
        return " / ".join(self.basis)


def _hit(pat: re.Pattern, label: str, text: str | None) -> str | None:
    if not text:
        return None
    m = pat.search(text)
    return f"{label}에 '{m.group(0)}': {T.excerpt(text, m.start(), m.end(), 30)}" if m else None


_SITUATION_AFTER = re.compile(r"^[^.。\n]{0,20}?(?:어려움|어려운|곤란|소외|제약)")


def _find_purpose(pat: re.Pattern, parts: dict[str, str], labels: tuple[str, ...]) -> str | None:
    """'취업 및 경제활동에 어려움을 겪는' 같은 처지 설명은 지원 목적 단서로 쓰지 않는다."""
    for lb in labels:
        t = parts.get(lb) or ""
        for m in pat.finditer(t):
            if _SITUATION_AFTER.match(t[m.end():]):
                continue
            return f"{lb}에 '{m.group(0)}': {T.excerpt(t, m.start(), m.end(), 30)}"
    return None


def _find(pat: re.Pattern, parts: dict[str, str], labels: tuple[str, ...]) -> str | None:
    for lb in labels:
        h = _hit(pat, lb, parts.get(lb))
        if h:
            return h
    return None



# 그 자체로 개인 주거 비용인 항목 / 주거용인지 사업용인지 문맥으로 정해야 하는 항목
_HOME_ONLY = re.compile(r"월세|전세|전월세|주거비|이사\s*비용?|이주\s*비용|중개\s*수수료|주거\s*급여|주거\s*지원비|주택\s*보조금|"
                        r"(?:주택|전세|전월세|주거)\s*(?:구입\s*|마련\s*)?(?:자금\s*)?(?:을|를)?\s*대출|주택\s*(?:구입|마련)\s*자금")
_HOME_CTX = r"주거|주택|거주|무주택|숙소|원룸|전입|이사|자취|하숙|기숙|임차인|입주|월세|전세"
_BIZ_CTX = r"사업장|점포|사무실|매장|상가|공장|작업장|창업\s*공간|창작\s*공간|업무\s*공간|공유\s*오피스|농지|축사|재배\s*시설|영농|" \
           r"창업\s*기업|청년\s*기업|창업자|사업자|소상공인|업체|기업에게|기업에\s*대해|청년몰|상인|농장|창업하여"


def _costs(parts: dict[str, str], labels: tuple[str, ...]):
    """비용 표현마다 (근거, 개인 주거비, 사업 비용, 지원 동사 있음). 주변 30자 문맥과 정책명·지원목적의 문맥으로 정한다."""
    head_ctx = _STARTUP_INCIDENTAL.sub(" ", f"{parts.get('정책명') or ''} {parts.get('지원목적') or ''}")
    policy_biz = bool(re.search(_BIZ_CTX, head_ctx))
    policy_home = bool(re.search(_HOME_CTX, head_ctx))
    body_ctx = _STARTUP_INCIDENTAL.sub(" ", parts.get("지원내용") or "")
    body_biz, body_home = bool(re.search(_BIZ_CTX, body_ctx)), bool(re.search(_HOME_CTX, body_ctx))
    out = []
    for lb in labels:
        t = parts.get(lb) or ""
        for m in _COST_WORD.finditer(t):
            before, after = t[max(0, m.start() - 30):m.start()], t[m.end():]
            if _PLAN_AFTER.match(after) or _NEG_AFTER.match(after):
                continue                     # '주거비 지원계획 수립' 같은 연혁·계획 문구, '사무실 임차료 사용 불가' 같은 제외 문구
            win = _STARTUP_INCIDENTAL.sub(" ", before + " " + after[:30])
            win_biz, win_home = bool(re.search(_BIZ_CTX, win)), bool(re.search(_HOME_CTX, win))
            word = m.group(0).replace(" ", "")
            if word in ("대출이자",) and not win_home:
                continue                     # 대상이 적히지 않은 '대출이자'(결혼비용·학자금 등)는 주거비 단서로 보지 않음
            if _HOME_ONLY.fullmatch(word) or _HOME_ONLY.match(m.group(0)):
                biz = win_biz and not win_home          # '상가 임대차계약서상 월세' 같은 예외만 사업용
            elif win_home and not win_biz:
                biz = False
            elif win_biz and not win_home:
                biz = True
            elif policy_biz != policy_home:
                biz = policy_biz                         # 주변 문맥이 없으면 정책명·지원목적의 문맥으로
            elif not policy_biz and body_biz != body_home:
                biz = body_biz                           # 그래도 없으면 지원내용 전체의 문맥으로
            else:
                biz = None                               # 주거용·사업용을 구분할 수 없음 → 분류 검토
            give = (bool(_GIVE_AFTER.match(after)) or (lb == "정책명" and bool(_TITLE_GIVE.search(t)))
                    or bool(re.search(r"[을를]\s*대출$", m.group(0))))     # '주택구입자금을 대출'처럼 표현 자체가 지원 행위
            ev = f"{lb}에 '{m.group(0)}': {T.excerpt(t, m.start(), m.end(), 30)}"
            out.append((ev, biz is False, biz is True, give, biz is None))
    return out


def aid_kind(title: str, content: str, purpose: str = "") -> tuple[str, str]:
    """장학금 분야 안의 지원 유형과 근거. 채무조정 → 이자지원 → 기존 대출 상환지원 → 새 대출 순으로 본다."""
    parts = {"정책명": title, "지원목적": purpose, "지원내용": content}
    for kind, pat in ((AID_RECOVERY, _AID_RECOVERY), (AID_INTEREST, _AID_INTEREST), (AID_REPAY, _AID_REPAY), (AID_LOAN, _AID_LOAN)):
        h = _find(pat, parts, ("정책명", "지원목적", "지원내용"))
        if h:
            return kind, h
    return AID_SCHOLAR, "정책명·지원목적·지원내용에 대출·이자·상환·채무조정 표현 없음"


def decide(row: dict, *, include_public_rental: bool = False) -> Decision:
    g = lambda k: T.clean(row.get(k))
    title, purpose, content = g("plcyNm") or "", g("plcyExplnCn") or "", g("plcySprtCn") or ""
    lcls, mcls, kw = g("lclsfNm"), g("mclsfNm"), g("plcyKywdNm")
    fields_note = f"(보조 근거) 온통청년 분류값: 대분류 {lcls or '없음'} / 중분류 {mcls or '없음'} / 키워드 {kw or '없음'}"
    if not g("plcyNo") or not title:
        return Decision(EXCLUDE, None, EX_NO_ID, [fields_note])
    P = {"정책명": title, "지원목적": purpose, "지원내용": content}
    HEAD, ALL, BODY = ("정책명", "지원목적"), ("정책명", "지원목적", "지원내용"), ("지원목적", "지원내용")
    head = f"{title} {purpose}"
    b = lambda *xs: [x for x in xs if x] + [fields_note]

    if T.PUBLIC_RENTAL.search(title) and not include_public_rental:
        return Decision(EXCLUDE, "주거비", EX_RENTAL, b(_hit(T.PUBLIC_RENTAL, "정책명", title)))

    money = _find(_MONEY, P, ("지원내용",))
    ev = _hit(_EVENT, "정책명", title)
    if ev:
        if money:
            return Decision(REVIEW, None, RV_EVENT_MONEY, b(ev, money))
        return Decision(EXCLUDE, None, EX_EVENT, b(ev, "지원내용에 금액·지급 표현 없음"))

    # ---- 수혜자: 기업·기관에 주는 지원인지
    emp = _hit(_EMPLOYER, "지원목적·지원내용", f"{purpose} {content}")
    labor = _hit(_LABOR_COST, "지원목적·지원내용", f"{purpose} {content}")
    ind = _hit(_INDIVIDUAL, "지원목적·지원내용", f"{purpose} {content}")
    if emp and labor:
        if ind:
            return Decision(REVIEW, None, RV_EMPLOYER_MIXED, b(emp, ind))
        return Decision(EXCLUDE, None, EX_EMPLOYER, b(emp, labor, "수혜자: 청년 개인에게 지급한다는 표현 없음"))
    personal = ind or _find(_PERSONAL_PAY, P, BODY)
    youth_ind = personal or ("정책명·지원목적에 청년·대학생 대상 표기" if ("청년" in head or "대학생" in head) else None)

    # ---- 실제 지원 비용: 개인 주거비 / 사업 비용
    costs = _costs(P, ALL)
    home_cost = next((c[0] for c in costs if c[1] and c[3]), None)       # 개인 주거용 비용을 지원
    biz_place = next((c[0] for c in costs if c[2]), None)                # 사업장·점포 등의 비용
    # 사업화 자금 등: 정책명·지원목적에 있거나, 지원내용에 있으면서 개인 수혜 표현이 없을 때만 사업 비용으로 본다
    # (개인 대출의 자금 용도 목록 중 하나로 '창업자금'이 나오는 경우 등은 제외)
    biz_fund = _find(_BIZ_FUND, P, HEAD) or (None if personal else _find(_BIZ_FUND, P, ("지원내용",)))
    cost_unclear = next((c[0] for c in costs if c[4] and c[3]), None)
    # 창업 단서: 부수 언급(취·창업 병기 등)을 지운 뒤 정책명·지원목적·지원내용을 모두 확인
    Pn = {k: _STARTUP_INCIDENTAL.sub(" ", v) for k, v in P.items()}
    startup_head = _find_purpose(_STARTUP, Pn, HEAD)
    startup_any = startup_head or _find(_STARTUP, Pn, ("지원내용",))

    # ---- 지원 목적(범위 안)
    sch = _find(_SCH_TITLE, P, ("정책명",)) or _find(_SCH_BODY, P, ("지원내용",))
    strong = _find(_LIVING_STRONG, P, ALL)
    strong_t = _find(_LIVING_STRONG, P, ("정책명",))
    generic = _find(_LIVING_GENERIC, P, ALL)
    food = _find(_FOOD_INKIND, P, ALL)
    cash = _find(_CASH, P, ("지원내용",))
    live_purpose = _find(_LIVING_PURPOSE, P, HEAD)
    cat = "장학금" if sch else "주거비" if home_cost else "생활비" if (strong or generic or food or (live_purpose and personal and cash)) else None

    deduct = _find(_DEDUCT, P, ALL)
    supply = _find(_SUPPLY, P, HEAD) or _find(_HOUS_PROVIDE, P, HEAD)
    job, asset = (_find_purpose(x, P, HEAD) for x in (_JOB, _ASSET))
    # 의료·출산·육아: 정책명에 있거나, 지원목적에 있으면서 실제 지원내용도 그 항목일 때만 주된 목적으로 본다
    # ('결혼친화환경 조성 및 출산·육아 등 정착기반' 같은 기대 효과 문구는 제외)
    med = _find_purpose(_MED_BIRTH, P, ("정책명",)) or (
        _find_purpose(_MED_BIRTH, P, ("지원목적",)) if _find(_MED_BIRTH, P, ("지원내용",)) else None)
    loan_int = _find(re.compile(r"(?:기\s*납부|대출)\s*이자|이자\s*(?:지원|보전)|이차\s*보전"), P, ALL)
    asset_method = _find(_ASSET_METHOD, P, ("지원내용",))          # 지원방식이 적립·매칭이면 자산형성
    checked = ("다른 목적 확인: 정책명·지원목적·지원내용에서 사업화·사업장 비용, 소득공제 표현 없음(취·창업 병기 등 부수 언급 제외) / "
               "정책명·지원목적에서 주택 공급, 의료·출산·육아, 취업·직무, 자산형성 표현 없음")

    # ---- 장학금: 정책명 단서가 있으면 창업 언급과 무관하게 장학금 분야, 지원유형을 구분
    if cat == "장학금" and not (biz_fund and not _find(_SCH_TITLE, P, ("정책명",))):
        kind, kev = aid_kind(title, content, purpose)
        return Decision(INCLUDE, "장학금", "", b(f"지원 목적: {sch}", f"지원 유형: {kind} — {kev}",
                                                  f"수혜자: {youth_ind or '원문 확인'}"), aid_kind=kind)

    # ---- 자산형성이 주된 방식이면(부수적 소득공제 설명이 있어도) 자산형성 사유 유지
    if (asset and (cat or cash) and not home_cost) or (asset_method and cat == "생활비"):
        return Decision(REVIEW, "생활비" if cat else None, RV_ASSET, b(asset or asset_method, deduct, money))
    if deduct and not strong_t and not home_cost:
        return Decision(REVIEW, cat, RV_DEDUCTION, b(deduct, "지원방식: 제도상 공제·감면 — 현금 지급과 구분", money))

    # ---- 주거비: 개인 주거용 비용 지원이 실제 내용이면 포함(공공임대·숙소·창업자 언급만으로 보류하지 않음)
    if home_cost:
        to_emp = _find(_TO_EMPLOYER, P, BODY)
        if to_emp:
            return Decision(REVIEW, "주거비", RV_EMPLOYER_HOUSING, b(to_emp, home_cost))
        return Decision(INCLUDE, "주거비", "", b(f"지원 목적·비용: {home_cost}", f"수혜자: {youth_ind or '원문 확인'}",
                                                  f"참고: {supply or startup_any}" if (supply or startup_any) else None))
    biz_give = next((c[0] for c in costs if c[2] and c[3]), None) or biz_fund
    if biz_give:
        # 실제 지원 비용이 사업 비용: 개인 생활비 지급 표현이 함께 있으면 분류 검토, 없으면 범위 밖으로 제외
        Pnb = {k: _BIZ_FUND.sub(" ", v) for k, v in P.items()}          # '사업화 지원금' 같은 사업 비용 표현을 지운 뒤 확인
        g2 = _find(_LIVING_GENERIC, Pnb, ALL)
        personal_living = (strong if strong and not _find(_WAGE, P, ALL) else None) or g2
        if personal_living:
            return Decision(REVIEW, None, RV_BUSINESS, b(biz_give, personal_living, startup_any))
        return Decision(EXCLUDE, None, EX_BUSINESS, b(biz_give, startup_any, "개인 주거비·생활비 지급 표현 없음"))
    if cost_unclear and not supply:
        return Decision(REVIEW, "주거비", RV_COST_UNCLEAR, b(cost_unclear, startup_any))
    if supply:
        return Decision(REVIEW, "주거비", RV_HOUSING_SUPPLY if _find(_SUPPLY, P, HEAD) else RV_HOUSING_PROVIDE,
                        b(supply, "지원목적·지원내용에 개인 주거비(월세·보증금·대출이자·이사비 등) 지원 표현 없음"))

    if cat == "생활비" or cash:
        cue = strong or generic or food or live_purpose or cash
        if med:
            return Decision(REVIEW, "생활비", RV_MEDICAL_BIRTH, b(med, cue, money))
        if strong_t:   # 정책명 자체가 생활비 항목(구직활동 지원금·교통비 등)
            return Decision(INCLUDE, "생활비", "", b(f"지원 목적: {strong_t}", f"지원방식: {money or '정책명 기준'}",
                                                      f"수혜자: {youth_ind or '원문 확인'}"))
        if startup_head:
            return Decision(REVIEW, None, RV_STARTUP, b(startup_head, cue))
        if food and not (strong or generic):
            # 식사 제공 등 현물: 금액이 있어도 본인 부담 가격일 수 있어 지급으로 보지 않음
            return Decision(REVIEW, "생활비", RV_INKIND, b(food, cash or "지원내용에 금액 표기 없음"))
        wage = _find(_WAGE, P, ALL)
        if job or (wage and not strong):
            return Decision(REVIEW, "생활비", RV_JOB, b(job or f"{wage} — 근로·활동의 대가", cue))
        if (strong or generic) and money and youth_ind:
            return Decision(INCLUDE, "생활비", "", b(f"지원 목적: {strong or generic}", f"지원방식: {money}",
                                                      f"수혜자: {youth_ind}", checked))
        if live_purpose and personal and cash:
            return Decision(INCLUDE, "생활비", "", b(f"지원 목적: {live_purpose}", f"지원방식: {cash}", f"수혜자: {personal}", checked))
        if strong or generic:
            return Decision(REVIEW, "생활비", RV_GENERIC_MONEY, b(cue, money or "지원내용에 금액·지급 표현 없음"))
        if loan_int:
            return Decision(REVIEW, None, RV_LOAN_INTEREST, b(loan_int, cash))
        return Decision(REVIEW, None, RV_CASH_ONLY, b(cash))

    weak = _find(_SCH_WEAK, P, BODY)
    if weak:
        return Decision(REVIEW, "장학금", RV_BODY_TUITION, b(weak))
    if any("주거" in (x or "") for x in (lcls, mcls)):
        return Decision(REVIEW, "주거비", RV_HOUSING_FIELD, b("온통청년 대분류·중분류에 '주거'(보조 근거만으로 포함하지 않음)"))
    return Decision(EXCLUDE, None, EX_NO_CUE, b())
