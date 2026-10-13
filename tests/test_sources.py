import pytest

from collector.sources.base import SourceUnavailable, UnexpectedResponse
from collector.sources.campuscholar import CampuScholar
from collector.sources.gov24 import Gov24
from collector.sources.held import linkareer, wevity
from collector.sources.youthcenter import YouthCenter
from tests.fakes import RSS, FakeResp, gov24_http, gov24_row, youth_row


def test_gov24_maps_youth_housing_item():
    http = gov24_http([gov24_row(), gov24_row(서비스ID="TEST2", 서비스명="[TEST] 노인 일자리", 지원대상="65세 이상", 선정기준="65세 이상 저소득 노인")],
                      detail_rows=[{"서비스ID": "TEST000001", "구비서류": "임대차계약서", "온라인신청사이트URL": "https://example.com/TEST"}])
    r = Gov24(service_key="TESTKEY", http=http).fetch()
    assert r.fetched_raw == 2 and len(r.items) == 1
    rec = r.items[0].to_record()
    assert rec["category"] == "주거비" and rec["region"] == "전국"
    assert rec["amount_text"] == "월 최대 20만 원" and rec["amount_qualifier"] == "최대"
    assert rec["apply_start"] == "2026-10-01" and rec["apply_end"] == "2026-10-31"
    assert rec["posted_at"] == "2026-09-01"          # 등록일시 ≠ 신청 시작일
    assert rec["required_docs"] == "임대차계약서"
    assert rec["extra"]["budget_end"] and "예산" in rec["extra"]["budget_end"]
    assert rec["support_type"] == "무상지원"
    assert r.skipped == {"청년·대학생 대상 아님": 1}


def test_gov24_missing_key_is_unconnected_not_failure():
    with pytest.raises(SourceUnavailable) as e:
        Gov24(service_key="").fetch()
    assert e.value.status == "미연결"


def test_gov24_unexpected_shape_fails_loudly():
    http = lambda url, params=None, **_: FakeResp({"error": "TEST"})
    with pytest.raises(UnexpectedResponse):
        Gov24(service_key="K", http=http).fetch()


def test_gov24_multiple_periods_marked_for_review():
    http = gov24_http([gov24_row(신청기한="정기: 2026. 5. 1. ~ 2026. 6. 1.\n추가: 2026. 6. 2. ~ 2026. 12. 1.")])
    rec = Gov24(service_key="K", http=http).fetch().items[0].to_record()
    assert rec["apply_start"] is None and rec["apply_end"] is None
    assert {"apply_start", "apply_end"} <= set(rec["needs_review_fields"])
    assert rec["period_text"].startswith("정기")


def test_youthcenter_maps_living_item():
    def http(url, params=None, **_):
        return FakeResp({"resultCode": 200, "result": {"pagging": {"totCount": 1, "pageNum": 1, "pageSize": 100},
                                                       "youthPolicyList": [youth_row()]}})
    r = YouthCenter(api_key="K", http=http).fetch()
    rec = r.items[0].to_record()
    assert rec["category"] == "생활비" and rec["region"] == "부산"
    assert (rec["apply_start"], rec["apply_end"]) == ("2026-10-05", "2026-10-24")
    assert rec["age_condition"] == "만 19~34세"
    assert rec["sources"][0]["url"] is None          # 상세 주소 규칙을 추측하지 않음


def test_youthcenter_no_apply_period_does_not_use_business_period():
    row = youth_row(aplyYmd="", bizPrdBgngYmd="20260101", bizPrdEndYmd="20261231")
    rec = YouthCenter(api_key="K").map_row(row)[0].to_record()
    assert rec["apply_start"] is None and rec["apply_end"] is None
    assert "apply_end" in rec["needs_review_fields"]


def test_campuscholar_held_until_confirmed():
    with pytest.raises(SourceUnavailable) as e:
        CampuScholar(confirmed=False).fetch()
    assert e.value.status == "연결 보류"


def test_campuscholar_rss_stores_facts_only():
    http = lambda url, params=None, **_: FakeResp(text=RSS)
    r = CampuScholar(confirmed=True, http=http).fetch()
    assert r.fetched_raw == 2 and len(r.items) == 1    # 고등학생 전용 제외
    rec = r.items[0].to_record()
    assert rec["benefit_text"] is None and rec["apply_end"] is None
    assert rec["organizer"] == "TEST장학재단"
    assert rec["extra"]["sch_kind"] == "생활비"
    assert "apply_end" in rec["needs_review_fields"]


def test_held_sources():
    for s in (linkareer(), wevity()):
        with pytest.raises(SourceUnavailable) as e:
            s.fetch()
        assert e.value.status == "연결 보류"
