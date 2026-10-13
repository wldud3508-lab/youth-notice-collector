from collector import textutil as T


def test_period_full_range_and_time():
    p = T.parse_period("2026. 9. 28.(월) ~ 10. 20.(화) 18:00까지")
    assert (p.start, p.end, p.end_time) == ("2026-09-28", "2026-10-20", "18:00")


def test_period_compact_and_always():
    p = T.parse_period("20261005 ~ 20261024")
    assert (p.start, p.end) == ("2026-10-05", "2026-10-24")
    assert T.parse_period("상시신청").always


def test_period_multiple_ranges_needs_review():
    p = T.parse_period("정기신청: 2026. 5. 1. ~ 2026. 6. 1.\n기한 후 신청: 2026. 6. 2. ~ 2026. 12. 1.")
    assert p.needs_review and p.start is None and p.end is None


def test_period_unclear_keeps_none():
    p = T.parse_period("10월 중 접수 예정 (추후 공지)")
    assert p.needs_review and p.start is None and p.end is None


def test_period_single_date_without_cue_is_not_guessed():
    p = T.parse_period("2026. 10. 15.")
    assert p.needs_review and p.start is None and p.end is None


def test_amount_preserves_qualifier_and_period():
    a = T.parse_amount("월 최대 20만 원, 최대 12개월 지급")
    assert a.text == "월 최대 20만 원"
    assert (a.value, a.period, a.qualifier) == (200000, "월", "최대")


def test_amount_multiple_values_flagged():
    a = T.parse_amount("대상 300만 원, 우수 50만 원")
    assert a.ambiguous


def test_support_type_loan_is_not_free_money():
    assert T.support_type("청년 전세자금 대출")[0] == "대출"
    assert T.support_type("보증금 대출 이자 지원")[0] == "이자지원"
    assert T.support_type("월세 지원금 지급")[0] == "무상지원"
    assert T.support_type("상담 프로그램 운영")[0] is None


def test_age_man_check():
    assert T.parse_age("만 19세 ~ 34세 청년")[0] == "만 19~34세"
    cond, _, need = T.parse_age("19~34세 청년")
    assert "만 나이 여부" in cond and need


def test_classify_excludes_public_rental_by_default():
    assert T.classify("행복주택 입주자 모집")[0] is None
    assert T.classify("행복주택 입주자 모집", include_public_rental=True)[0] == "주거비"
    assert T.classify("청년 월세 지원")[0] == "주거비"
    assert T.classify("청년 교통비 지원")[0] == "생활비"


def test_region():
    assert T.region_from_org("부산광역시 해운대구")[0] == "부산"
    assert T.region_from_org("국토교통부")[0] == "전국"
    assert T.region_from_codes("26110,26140")[0] == "부산"
    assert T.region_from_codes("11110,26110")[0] == "복수 지역"
