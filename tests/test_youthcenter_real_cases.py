"""2026-10-16 온통청년 실제 응답 원문으로 만든 회귀 시험.

원문은 tests/data/youthcenter_real_cases.json 에 그대로 두었고, 판정 규칙에는 정책번호·정책명 예외가 없다.
"""
import json
from pathlib import Path

import pytest

from collector.sources import youthcenter_rules as Y
from collector.sources.youthcenter import YouthCenter

ROWS = json.loads((Path(__file__).parent / "data" / "youthcenter_real_cases.json").read_text(encoding="utf-8"))["rows"]
INC, REV, EXC = Y.INCLUDE, Y.REVIEW, Y.EXCLUDE

CASES = [
    # (키, 판정, 분야 또는 None(확인 안 함), 사유 또는 None)
    # 1. 창업: 실제 지원 비용으로 구분
    ("tourdure", EXC, None, Y.EX_BUSINESS),                 # 사업화 지원금(지원내용에만 있음) — 개인 생활비 아님
    ("gwangsan_rent", EXC, None, Y.EX_BUSINESS),            # 사업장 월 임대료
    ("guarantee", EXC, None, Y.EX_BUSINESS),                # 창업기업 보증료
    ("farm_home", INC, "주거비", ""),                        # 창업농이어도 실제 지원은 월세·전세자금 대출이자
    ("job_allow", INC, "생활비", ""),                        # '취·창업으로 중단 시' 같은 병기 언급
    ("job_cost", INC, "생활비", ""),                         # '구직‧(예비) 창업 활동 지원금'
    ("welfare_point", INC, "생활비", ""),                    # 재직 사업장 설명 속 '소상공인업체'
    ("ladder", INC, "장학금", ""),                           # '취업·창업(희망)자를 위한 장학금'
    ("startup_pkg", EXC, None, None),                        # 범위 단서 없는 창업 정책은 제외 유지
    # 2. 주거비: 개인 주거 비용과 공간 제공·공급·사업장 구분
    ("deposit_interest", INC, "주거비", ""),
    ("sokcho_move", INC, "주거비", ""),
    ("gwanoe", INC, "주거비", ""),
    ("lucky7", INC, "주거비", ""),                           # 공공임대 언급이 있어도 실제 지원은 임대료·대출이자
    ("house30k", INC, "주거비", ""),
    ("goshiwon", INC, "주거비", ""),
    ("naju", INC, "주거비", ""),
    ("safe_deposit", INC, "주거비", ""),
    ("jeonse_guarantee", INC, "주거비", ""),                 # 개인 임차인의 전세보증 보증료
    ("safe_supply", REV, "주거비", Y.RV_HOUSING_SUPPLY),      # '주거비 지원계획 수립'은 연혁 문구
    ("worker_home", REV, "주거비", Y.RV_EMPLOYER_HOUSING),    # 기업에 지급
    ("cup_deposit", EXC, None, None),                        # '보증금제'는 주거비가 아님
    # 3. 생활비·학자금
    ("incentive", REV, None, Y.RV_DEDUCTION),
    ("leaving_care", INC, "생활비", ""),
    ("goyong_quit", INC, "생활비", ""),
    ("goyong_move", REV, None, None),
    ("tomorrow_saving", REV, None, Y.RV_ASSET),             # 부수적 '근로소득공제' 언급이 있어도 저축 매칭
    ("dream_account", REV, None, Y.RV_ASSET),
    ("loan_repay", INC, "장학금", ""),
    ("icl_tuition", INC, "장학금", ""),
    ("chungju", INC, "생활비", ""),
]


@pytest.mark.parametrize("key, verdict, cat, reason", CASES, ids=[c[0] for c in CASES])
def test_real_case_verdict(key, verdict, cat, reason):
    d = Y.decide(ROWS[key])
    assert d.verdict == verdict, (ROWS[key]["plcyNm"], d.verdict, d.reason, d.basis)
    if cat is not None:
        assert d.category == cat
    if reason is not None:
        assert d.reason == reason, d.basis


@pytest.mark.parametrize("key", ["art_space", "small_wedding", "wedding_loan", "cup_deposit", "gwangsan_rent", "guarantee"])
def test_not_included_as_housing_or_living_by_word(key):
    d = Y.decide(ROWS[key])
    assert d.verdict != INC, (ROWS[key]["plcyNm"], d.basis)


def test_wedding_cost_food_word_not_living_basis():
    d = Y.decide(ROWS["small_wedding"])
    assert "식비" not in d.basis_text()                      # '예식비용'의 '식비'를 근거로 쓰지 않음


def test_tourdure_basis_names_business_cost_from_content():
    d = Y.decide(ROWS["tourdure"])
    assert d.basis[0].startswith("지원내용에") and "사업화 지원금" in d.basis[0]    # 지원내용까지 확인한 근거


def test_aid_kinds_from_real_text():
    yc = YouthCenter(api_key="K")
    repay = yc.map_row(ROWS["loan_repay"])[0].to_record()
    assert repay["flags"]["aid_kind"] == "대출 상환지원" and repay["support_type"] == "무상지원"
    notes = " ".join(x["note"] for x in repay["review_notes"])
    assert "새 대출·새 상환 의무 아님" in notes and "상환 의무 있음" not in notes
    loan = yc.map_row(ROWS["icl_tuition"])[0].to_record()
    assert loan["flags"]["aid_kind"] == "학자금대출" and loan["support_type"] == "대출"
    assert any("상환 의무 있음" in x["note"] for x in loan["review_notes"])


def test_aux_deadline_real_text():
    yc = YouthCenter(api_key="K")
    ch = yc.map_row(ROWS["chungju"])[0].to_record()
    assert ch["period_kind"] == "사유 발생 후 기한" and ch["apply_start"] is None and ch["apply_end"] is None
    assert ch["flags"]["period_aux"]["field"] == "plcySprtCn"
    assert "6개월이 되는 날이 속한 달 말일까지 신청" in ch["flags"]["period_aux"]["sentence"]
    assert "2025" not in (ch["period_text"] or "")           # 사업기간(2025-01-06~2025-12-31)으로 대체하지 않음
    # '접수 후 … 30일 이내 지급'은 처리기한이라 신청기한으로 쓰지 않음
    from collector.sources.youthcenter import _aux_deadline
    assert _aux_deadline({"plcySprtCn": "지급일 : 접수 후 심사를 거쳐 30일 이내(기간 연장 필요한 경우 신청인 통지 후 15일 연장 가능) 지급"}) is None
    assert _aux_deadline({"plcySprtCn": "혼인신고일로부터 2년 이내 신청"})["verb"] == "신청"


def test_reason_matches_actual_purpose():
    # '결혼친화환경 조성 및 출산·육아 등' 같은 기대 효과 문구를 의료·출산 사유로 쓰지 않음
    d = Y.decide(ROWS["wedding_loan"])
    assert d.verdict == REV and d.reason == Y.RV_LOAN_INTEREST, d.basis
    # '취업 및 경제활동에 어려움을 겪는' 같은 처지 설명은 취업 목적 단서가 아님
    d = Y.decide({"plcyNo": "T", "plcyNm": "청년 부실채무자 지원", "plcyExplnCn": "유의자로 등록되어 취업 및 경제활동에 어려움을 겪는 청년",
                  "plcySprtCn": "초입금 지원(채무액의 10%, 100만원 한도)"})
    assert d.reason != Y.RV_JOB
    # '식생활 돌봄' 같은 일반 표현은 의료·육아 사유가 아님
    d = Y.decide({"plcyNo": "T", "plcyNm": "직장인 한끼", "plcyExplnCn": "직장인의 식생활 돌봄", "plcySprtCn": "점심 외식비용 20% 지원(월 4만원 한도)"})
    assert d.reason != Y.RV_MEDICAL_BIRTH


def test_review_items_marked_unconfirmed_not_in_db(tmp_path):
    import csv
    from collector.review_report import REVIEW_STATUS, write_review
    from tests.test_youthcenter_rules import _http
    yc = YouthCenter(api_key="TEST-DUMMY-KEY-0001", http=_http([ROWS["wedding_loan"], ROWS["lucky7"], ROWS["cup_deposit"]]))
    yc.today = "2026-10-16"
    r = yc.fetch()
    assert [it.fields["title"] for it in r.items] == [ROWS["lucky7"]["plcyNm"]]     # DB로 가는 것은 '포함'뿐
    write_review(yc, tmp_path, include_raw=False, secret="TEST-DUMMY-KEY-0001")
    md = (tmp_path / "youthcenter_report.md").read_text(encoding="utf-8")
    assert "미확정·DB 미반영" in md and "'제외'도 '검토 완료'도 아니며" in md
    rows = list(csv.DictReader((tmp_path / "youthcenter_review_needed.csv").open(encoding="utf-8-sig")))
    assert len(rows) == 1 and rows[0]["처리 상태"] == REVIEW_STATUS and rows[0]["판정"] == "분류 검토"


@pytest.mark.parametrize("key", ["policy_lab", "gap_year"])
def test_project_expense_not_confirmed_as_living(key):
    # '프로젝트당 지원금', '프로젝트 활동 비용(숙박비·교통비 포함)'은 개인 생활비로 확정하지 않음
    d = Y.decide(ROWS[key])
    assert d.verdict == REV and d.reason == Y.RV_PROJECT, d.basis


def test_keyword_order_only_is_not_a_change():
    from collector.store import raw_diff
    a, b = ROWS["kw_order_1016"], ROWS["kw_order_1018"]
    assert a["plcyKywdNm"] != b["plcyKywdNm"] and sorted(a["plcyKywdNm"].split(",")) == sorted(b["plcyKywdNm"].split(","))
    yc = YouthCenter(api_key="K")
    ia, ib = yc.map_row(a)[0], yc.map_row(b)[0]
    assert ia.source_digest == ib.source_digest                 # 원본 요약값 같음 → 원문 변경으로 기록하지 않음
    assert ib.raw["list"]["plcyKywdNm"] == b["plcyKywdNm"]       # 원본은 받은 그대로 보관
    assert not [c for c in raw_diff(ia.raw, ib.raw) if c["key"] == "plcyKywdNm"]


def test_summary_shows_loan_kind(tmp_path):
    from collector.export import cat_label
    rec = YouthCenter(api_key="K").map_row(ROWS["icl_tuition"])[0].to_record()
    assert cat_label(rec) == "장학금 · 학자금대출 · 상환 의무 있음"
    assert cat_label(YouthCenter(api_key="K").map_row(ROWS["loan_repay"])[0].to_record()) == "장학금 · 기존 대출 상환지원(새 대출 아님)"
