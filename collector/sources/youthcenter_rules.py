"""온통청년 정책의 수집 범위 판정(포함 / 분류 검토 / 제외).

원칙
- '지원금', '등록금' 같은 단어 하나로 분야를 정하지 않는다. 정책명·지원목적(정책설명)·지원내용·수혜자를 함께 본다.
- '인턴', '출산', '의료' 같은 단어만으로 제외하지 않는다. 그런 정책은 '분류 검토'로 보낸다.
- 자동 제외는 (1) 강연·행사이면서 개인 지급 표현이 없는 경우, (2) 기업·기관에만 지급되는 지원,
  (3) 공공임대 입주자 모집(기본 범위 밖), (4) 장학금·생활비·주거비 단서가 전혀 없는 경우뿐이다.
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

EXCLUDE_REASONS = [EX_NO_ID, EX_RENTAL, EX_EVENT, EX_EMPLOYER, EX_NO_CUE]
REVIEW_REASONS = [RV_EVENT_MONEY, RV_EMPLOYER_MIXED, RV_MEDICAL_BIRTH, RV_JOB, RV_STARTUP, RV_ASSET, RV_INKIND,
                  RV_HOUSING_PROVIDE, RV_HOUSING_FIELD, RV_BODY_TUITION, RV_GENERIC_MONEY, RV_CASH_ONLY]

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
                            r"교통비|통신비|식비|생활\s*지원금|긴급\s*(?:생계|복지)|난방비|에너지\s*바우처")
_LIVING_GENERIC = re.compile(r"지원금|장려금|수당|바우처|지역\s*화폐|포인트|상품권|현금")
_FOOD_INKIND = re.compile(r"아침밥|아침\s*식사|식사|급식|도시락|식단|먹거리|반찬")
_ASSET = re.compile(r"자산\s*형성|저축|적금|통장|도약\s*계좌|내일\s*저축|희망\s*키움|계좌\s*(?:매칭|지원)")
_MED_BIRTH = re.compile(r"의료비|진료|검사비|치료|난임|건강\s*검진|병원|수술|임신|임산부|출산|산후|산모|육아|보육|돌봄")
_JOB = re.compile(r"인턴|일경험|일\s*경험|직무|채용|(?<!미)취업|직업\s*훈련|훈련|자격증|어학|면접")
_STARTUP = re.compile(r"창업|사업화|기업가|사업\s*자금|점포|소상공인")
_CASH = re.compile(r"\d[\d,.]*\s*(?:만|천|백|억)?\s*원")


@dataclass
class Decision:
    verdict: str                       # 포함 / 분류 검토 / 제외
    category: str | None = None        # 포함: 분야, 분류 검토: 잠정 분야(없을 수 있음)
    reason: str = ""                   # 제외·분류 검토 사유(포함이면 빈 문자열)
    basis: list[str] = field(default_factory=list)   # 판단 근거

    def basis_text(self) -> str:
        return " / ".join(self.basis)


def _hit(pat: re.Pattern, label: str, text: str | None) -> str | None:
    if not text:
        return None
    m = pat.search(text)
    return f"{label}에 '{m.group(0)}': {T.excerpt(text, m.start(), m.end(), 30)}" if m else None


def decide(row: dict, *, include_public_rental: bool = False) -> Decision:
    g = lambda k: T.clean(row.get(k))
    title, purpose, content = g("plcyNm") or "", g("plcyExplnCn") or "", g("plcySprtCn") or ""
    lcls, mcls, kw = g("lclsfNm"), g("mclsfNm"), g("plcyKywdNm")
    fields_note = f"온통청년 분류값: 대분류 {lcls or '없음'} / 중분류 {mcls or '없음'} / 키워드 {kw or '없음'}"
    if not g("plcyNo") or not title:
        return Decision(EXCLUDE, None, EX_NO_ID, [fields_note])
    head = f"{title} {purpose}"             # 정책명 + 지원목적(정책설명)
    allt = f"{title} {purpose} {content}"
    b = lambda *xs: [x for x in xs if x] + [fields_note]

    if T.PUBLIC_RENTAL.search(title) and not include_public_rental:
        return Decision(EXCLUDE, "주거비", EX_RENTAL, b(_hit(T.PUBLIC_RENTAL, "정책명", title)))

    money = _hit(_MONEY, "지원내용", content)
    ev = _hit(_EVENT, "정책명", title)
    if ev:
        if money:
            return Decision(REVIEW, None, RV_EVENT_MONEY, b(ev, money))
        return Decision(EXCLUDE, None, EX_EVENT, b(ev, "지원내용에 금액·지급 표현 없음"))

    emp = _hit(_EMPLOYER, "지원목적·지원내용", f"{purpose} {content}")
    labor = _hit(_LABOR_COST, "지원목적·지원내용", f"{purpose} {content}")
    ind = _hit(_INDIVIDUAL, "지원목적·지원내용", f"{purpose} {content}")
    if emp and labor:
        if ind:
            return Decision(REVIEW, None, RV_EMPLOYER_MIXED, b(emp, ind))
        return Decision(EXCLUDE, None, EX_EMPLOYER, b(emp, labor, "청년 개인에게 지급한다는 표현 없음"))

    # ---- 장학금: 정책명에 장학·학자금·등록금, 또는 지원내용에 장학금 지급·장학생 선발
    sch = _hit(_SCH_TITLE, "정책명", title) or _hit(_SCH_BODY, "지원내용", content)
    if sch:
        return Decision(INCLUDE, "장학금", "", b(sch))

    # ---- 주거비: 정책명에 주거비 항목이 있거나, 지원목적·내용에서 그 항목을 '지원·대출'한다고 적은 경우
    hc = _hit(_HOUS_TITLE, "정책명", title) or _hit(_HOUS_GIVE, "지원목적·지원내용", f"{purpose} {content}")
    if hc:
        return Decision(INCLUDE, "주거비", "", b(hc))
    hp = _hit(_HOUS_PROVIDE, "정책명·지원목적", head)
    if hp:
        return Decision(REVIEW, "주거비", RV_HOUSING_PROVIDE, b(hp))

    # ---- 생활비: 단어 하나로 정하지 않는다. 다른 주된 목적이 있으면 분류 검토
    med = _hit(_MED_BIRTH, "정책명·지원목적", head)
    job = _hit(_JOB, "정책명·지원목적", head)
    startup = _hit(_STARTUP, "정책명·지원목적", head)
    asset = _hit(_ASSET, "정책명·지원목적", head)
    strong = _hit(_LIVING_STRONG, "정책명·지원목적", head) or _hit(_LIVING_STRONG, "지원내용", content)
    generic = _hit(_LIVING_GENERIC, "정책명·지원목적·지원내용", allt)
    food = _hit(_FOOD_INKIND, "정책명·지원목적·지원내용", allt)
    cash = _hit(_CASH, "지원내용", content)
    if strong or generic or food or cash:
        cue = strong or generic or food or cash
        if med:
            return Decision(REVIEW, "생활비", RV_MEDICAL_BIRTH, b(med, cue, money))
        if asset:
            return Decision(REVIEW, "생활비", RV_ASSET, b(asset, money))
        st_title = _hit(_LIVING_STRONG, "정책명", title)
        if st_title:   # 정책명 자체가 생활비 항목(구직활동 지원금·교통비 등)
            return Decision(INCLUDE, "생활비", "", b(st_title, money))
        if startup:
            return Decision(REVIEW, None, RV_STARTUP, b(startup, cue))
        if job:
            return Decision(REVIEW, "생활비", RV_JOB, b(job, cue))
        if food and not (strong or generic):
            # 식사 제공 등 현물: 금액이 있어도 본인 부담 가격일 수 있어 지급으로 보지 않음
            return Decision(REVIEW, "생활비", RV_INKIND, b(food, cash or "지원내용에 금액 표기 없음"))
        if strong and money:
            return Decision(INCLUDE, "생활비", "", b(strong, money))
        if generic and money and (ind or "청년" in head or "대학생" in head):
            return Decision(INCLUDE, "생활비", "", b(generic, money, ind or "정책명·지원목적에 청년·대학생 대상 표기",
                                                      "다른 주된 목적(의료·출산·취업·창업·자산형성) 표현 없음"))
        if strong or generic:
            return Decision(REVIEW, "생활비", RV_GENERIC_MONEY, b(cue, money or "지원내용에 금액·지급 표현 없음"))
        return Decision(REVIEW, None, RV_CASH_ONLY, b(cash))

    weak = _hit(_SCH_WEAK, "지원목적·지원내용", f"{purpose} {content}")
    if weak:
        return Decision(REVIEW, "장학금", RV_BODY_TUITION, b(weak))
    if any("주거" in (x or "") for x in (lcls, mcls)):
        return Decision(REVIEW, "주거비", RV_HOUSING_FIELD, b("온통청년 대분류·중분류에 '주거'"))
    return Decision(EXCLUDE, None, EX_NO_CUE, b())
