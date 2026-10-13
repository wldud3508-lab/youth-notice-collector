"""분류 규칙 테스트. 문구는 1차 실제 수집(2026-10-13, 정부24)의 신청기한 원문에서 가져옴."""
import pytest

from collector import rules as R
from collector.export import is_candidate

T = "2026-10-13"


def st(text):
    r = R.classify_period(text)
    n = {"apply_start": r.start, "apply_end": r.end, "period_kind": r.kind,
         "period_month_end": r.month_end, "period_closed_year": r.closed_year}
    return r, R.apply_status(n, T)


@pytest.mark.parametrize("text,start,end", [
    ("2026 4. 20.(월) ~ 11.30. (월) 23시까지", "2026-04-20", "2026-11-30"),
    ("'26. 2. 9. ~ 12. 31.(예산소진시 마감)", "2026-02-09", "2026-12-31"),
    ("(2026년) 4. 20.~5.4.", "2026-04-20", "2026-05-04"),
    ("'25.12.22.~'26.12.11.", "2025-12-22", "2026-12-11"),
])
def test_newly_extracted_dates(text, start, end):
    r, _ = st(text)
    assert (r.kind, r.start, r.end) == (R.KIND_DATED, start, end)


def test_reversed_dates_are_not_used():
    r, s = st("2026.01.15.~2025.12.15.")
    assert r.kind == R.KIND_BAD and r.start is None and s == R.ST_UNCLEAR


@pytest.mark.parametrize("text,kind,status", [
    ("상시신청", R.KIND_ALWAYS, R.ST_ALWAYS),
    ("장학금별 접수시기 상이(홈페이지 공지사항 내 공고문 수시 참고)", R.KIND_BY_ORG, R.ST_UNCLEAR),   # '수시'를 상시로 보지 않음
    ("접수기관 별 상이", R.KIND_BY_ORG, R.ST_UNCLEAR),
    ("공고 시", R.KIND_NOTICE, R.ST_UNCLEAR),
    ("매년 10~11월 중", R.KIND_RECUR, R.ST_UNCLEAR),
    ("등록금 납입일로부터 6개월 이내", R.KIND_EVENT, R.ST_UNCLEAR),
    ("별도의 신청절차가 없음", R.KIND_NO_APPLY, R.ST_NO_APPLY),
    ("예산소진시까지", R.KIND_BUDGET, R.ST_UNCLEAR),
    ("2026.7~8월중", R.KIND_MONTH, R.ST_ELAPSED),          # 끝 달(8월)이 완전히 지남 → '표기된 일정 경과'
    ("2025. 8. ~ 2026. 2.", R.KIND_MONTH, R.ST_ELAPSED),
    ("2023년 8월 예정", R.KIND_MONTH, R.ST_UNCLEAR),        # '예정'은 지난 일정으로 보지 않음
    ("2026년 2월 ~ 예산 소진 시까지", R.KIND_MONTH, R.ST_UNCLEAR),   # 끝이 열려 있음
    ("2026년 1월~12월 중 예산 소진 시까지", R.KIND_MONTH, R.ST_UNCLEAR),   # 12월은 아직 안 지남
    ("2026년은 신규 대상자 미 모집", R.KIND_CLOSED, R.ST_CLOSED_TEXT),
    ("금년 접수 마감", R.KIND_CLOSED_REL, R.ST_UNCLEAR),    # 작성 연도 불명 → 올해 종료로 단정 안 함
])
def test_period_kinds(text, kind, status):
    r, s = st(text)
    assert (r.kind, s) == (kind, status)
    assert r.start is None and r.end is None               # 날짜를 만들지 않음


@pytest.mark.parametrize("text,month_end", [
    ("2026.7~8월중", "2026-08"),
    ("2025. 8. ~ 2026. 2.", "2026-02"),
    ("2026년 1월(상반기), 7월(하반기)", "2026-07"),     # 실제 원문: 마지막 달은 7월
    ("2026.10 ~11월중", "2026-11"),
    ("2026년 11월~2월", "2027-02"),
    ("2026년 2월 ~ 예산 소진 시까지", None),
    ("2026. 4월~", None),                               # 실제 원문: 끝이 열려 있음
    ("2024. 5. ~", None),
])
def test_month_end(text, month_end):
    assert R.classify_period(text).month_end == month_end


def test_elapsed_only_when_month_fully_past():
    r, _ = st("2026.9~10월중")
    n = {"period_kind": r.kind, "period_month_end": r.month_end}
    assert R.apply_status(n, "2026-10-31") == R.ST_UNCLEAR   # 10월 중에는 아직 아님
    assert R.apply_status(n, "2026-11-01") == R.ST_ELAPSED


def test_budget_flag_kept_with_dates():
    r, s = st("'26. 2. 9. ~ 12. 31.(예산소진시 마감)")
    assert R.FLAG_BUDGET in r.flags and s == R.ST_OPEN


def test_age_and_amount_rules():
    a = R.age_fields("19~34세 청년", None)
    assert a["age_condition"] == "19~34세" and not a["need"] and R.NOTE_AGE_BASIS in a["notes"]
    a = R.age_fields("만 19세 ~ 34세", (19, 39))
    assert a["need"] and a["conflict"]["field"] == "age_condition"
    m = R.amount_fields("청년 1인 월 20만원(최대 12개월, 총 240만원)")
    assert not m["need"] and len(m["amount_items"]) == 2 and m["amount_value"] is None
    assert R.amount_fields("월 20만 원 또는 월 30만 원")["need"]
    assert not R.amount_fields("기본 50만원 / 다자녀 100만원")["need"]


def test_candidate_rules():
    base = {"youth_relevant": True, "apply_start": "2026-10-01", "apply_end": "2026-10-31",
            "needs_review_fields": [], "conflicts": [], "review_status": "미검토"}
    assert is_candidate(base, T)
    assert not is_candidate({**base, "apply_end": "2026-10-01"}, T)                       # 마감
    assert not is_candidate({**base, "conflicts": [{"field": "age_condition", "values": []}]}, T)
    assert not is_candidate({**base, "youth_relevant": None}, T)
    assert is_candidate({**base, "needs_review_fields": ["support_type"]}, T)             # 중요 충돌 아님
    assert not is_candidate({**base, "apply_start": None, "apply_end": None, "period_kind": "상시"}, T)
