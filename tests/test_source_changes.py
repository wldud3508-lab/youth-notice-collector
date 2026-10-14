"""원본 변경 구분 테스트. 정부24 모듈을 통해 실제 필드 이름으로 가짜 응답(TEST)을 넣는다."""
from datetime import datetime, timedelta

from collector.models import KST, Item
from collector.sources.gov24 import Gov24
from collector.store import (NOTE_BACKFILL, NOTE_CORE, NOTE_META, NOTE_UNKNOWN, NOTE_UNVERIFIED,
                             REVIEW_FLAG_FIELD, Store)
from tests.fakes import gov24_http, gov24_row

LONG_HEAD = "가" * 650     # 화면용 600자를 넘기는 앞부분


def item(**row):
    return Gov24(service_key="K", http=gov24_http([gov24_row(**row)])).fetch().items[0]


def day(n):
    return datetime(2026, 10, 14, 9, 0, tzinfo=KST) + timedelta(days=n)


def run(st, it, n):
    return st.upsert(it, day(n).date().isoformat(), day(n))


def history(st):
    return [h["note"] for h in st.all_records()[0]["history"]]


def test_change_after_600_chars_is_core_change(tmp_path):
    st = Store(tmp_path / "t.db")
    run(st, item(지원내용=LONG_HEAD + " 월 최대 20만 원 지급"), 0)
    before = st.all_records()[0]
    r = run(st, item(지원내용=LONG_HEAD + " 월 최대 30만 원 지급"), 1)
    rec = st.all_records()[0]
    assert r == "changed" and rec["history"][0]["note"] == NOTE_CORE
    assert rec["benefit_text"] == before["benefit_text"]                      # 화면용 앞부분은 같음
    ch = [c for c in rec["history"][0]["changes"] if c["field"] == "원본 지원내용 [목록]"][0]
    assert ch["before"].endswith("20만 원 지급") and ch["after"].endswith("30만 원 지급")   # 이전·이후 전체 값
    assert rec["amount_text"] == "월 최대 30만 원"
    assert "review_reset_at" in rec["flags"] and rec["last_changed_at"]


def test_period_only_change_is_core(tmp_path):
    st = Store(tmp_path / "t.db")
    run(st, item(), 0)
    assert run(st, item(신청기한="2026. 10. 1. ~ 2026. 11. 15."), 1) == "changed"
    rec = st.all_records()[0]
    assert rec["history"][0]["note"] == NOTE_CORE and rec["apply_end"] == "2026-11-15"


def test_eligibility_only_change_is_core(tmp_path):
    st = Store(tmp_path / "t.db")
    run(st, item(), 0)
    assert run(st, item(선정기준="청년가구 기준 중위소득 50% 이하. 주거급여 수급자 제외"), 1) == "changed"
    assert history(st)[0] == NOTE_CORE


def test_modified_time_only_is_meta_and_keeps_review(tmp_path):
    st = Store(tmp_path / "t.db")
    run(st, item(), 0)
    assert run(st, item(수정일시="20261015090000"), 1) == "meta_changed"
    rec = st.all_records()[0]
    assert rec["history"][0]["note"] == NOTE_META
    assert rec["history"][0]["changes"][0]["field"] == "원본 수정일시 [목록]"
    assert "review_reset_at" not in rec["flags"] and rec["last_changed_at"] is None


def test_view_count_only_is_not_recorded(tmp_path):
    st = Store(tmp_path / "t.db")
    run(st, item(), 0)
    assert run(st, item(조회수=99999), 1) == "same"
    assert history(st) == ["최초 수집"]


def test_first_raw_storage_with_different_digest_is_unknown(tmp_path):
    """이전 요약값은 있으나 이전 원본이 없어 비교 불가 → 확인 불가, 미검토, 추가 검토 표시."""
    st = Store(tmp_path / "t.db")
    base = {"category": "생활비", "title": "T", "benefit_text": "월 10만 원"}
    st.upsert(Item("a", "TEST-a", "1", None, None, base, source_digest="d1"), "2026-10-13", day(-1))   # 원본 미보관
    r = st.upsert(Item("a", "TEST-a", "1", None, None, base, source_digest="d2", raw={"list": {"지원내용": "TEST"}}),
                  "2026-10-14", day(0))
    rec = st.all_records()[0]
    assert r == "changed" and st.last_unverified
    assert rec["history"][0]["note"] == NOTE_UNKNOWN
    assert REVIEW_FLAG_FIELD in rec["needs_review_fields"]


def test_migration_marks_only_differing_backfills_once(tmp_path):
    """10/13 요약값 → 10/14 원본 첫 보관(요약값 다름/같음) 상황을 재현해 1회성 점검을 확인."""
    st = Store(tmp_path / "t.db")
    for sid, d2 in (("1", "d1"), ("2", "dX")):
        st.upsert(Item("g", "TEST-g", sid, None, None, {"category": "생활비", "title": "T" + sid}, source_digest="d1"),
                  "2026-10-13", day(-1))
    # 10/14 (옛 코드 동작): 화면 정보 같음 → 원문 보완만 기록, 요약값은 새 값으로 덮임
    for sid, d2 in (("1", "d1"), ("2", "dX")):
        nid = st.db.execute("SELECT id FROM notices WHERE source_id=?", (sid,)).fetchone()[0]
        st.db.execute("INSERT INTO raw_sources VALUES (?,?,?,?,?,?)", (nid, d2, 1, "2026-10-14T09:27:00+09:00", "2026-10-14", "{}"))
        st._version(nid, "2026-10-14", "2026-10-14T09:27:00+09:00", NOTE_BACKFILL, [], {"flags": {"source_digest": d2}})
    assert st.migrate_unverified_backfill(day(0)) == 1
    assert st.migrate_unverified_backfill(day(0)) == 0            # 두 번 실행해도 반복 기록 없음
    recs = {r["sources"][0]["source_id"]: r for r in st.all_records()}
    assert recs["2"]["history"][0]["note"] == NOTE_UNVERIFIED
    assert REVIEW_FLAG_FIELD in recs["2"]["needs_review_fields"]
    assert recs["2"]["last_changed_at"] is None                   # 변경 시각을 만들지 않음
    assert recs["1"]["history"][0]["note"] == NOTE_BACKFILL


def test_clip_flag_appears_and_disappears_without_change_record(tmp_path):
    st = Store(tmp_path / "t.db")
    run(st, item(구비서류="TEST 서류"), 0)
    assert "clipped_fields" not in st.all_records()[0]["flags"]
    # 구비서류(상세)만 길어짐 → 원본 핵심 변경으로 기록되고 축약 표시가 생김
    long_docs = Gov24(service_key="K", http=gov24_http([gov24_row()], detail_rows=[{"서비스ID": "TEST000001", "구비서류": "서" * 700}]))
    run(st, long_docs.fetch().items[0], 1)
    assert st.all_records()[0]["flags"]["clipped_fields"] == ["required_docs"]
    # 같은 원본을 다시 처리 → 표시는 유지, 이력 추가 없음
    n = len(history(st))
    run(st, Gov24(service_key="K", http=gov24_http([gov24_row()], detail_rows=[{"서비스ID": "TEST000001", "구비서류": "서" * 700}])).fetch().items[0], 2)
    assert len(history(st)) == n and st.all_records()[0]["flags"]["clipped_fields"] == ["required_docs"]


def test_clip_flag_refreshed_on_unchanged_source(tmp_path):
    """원본이 그대로여도 매 실행 축약 표시를 다시 계산(기존 기록에 표시가 빠져 있던 문제)."""
    st = Store(tmp_path / "t.db")
    it = item(지원내용=LONG_HEAD)
    run(st, it, 0)
    nid = st.all_records()[0]["id"]
    import json
    rec = json.loads(st.db.execute("SELECT record_json FROM notices WHERE id=?", (nid,)).fetchone()[0])
    rec["flags"].pop("clipped_fields")                                # 옛 코드로 저장된 상태 재현
    st.db.execute("UPDATE notices SET record_json=? WHERE id=?", (json.dumps(rec, ensure_ascii=False), nid))
    n = len(history(st))
    assert run(st, item(지원내용=LONG_HEAD), 1) == "same"
    assert st.all_records()[0]["flags"]["clipped_fields"] == ["benefit_text"] and len(history(st)) == n
    # 다른 공고 형태: 축약이 더 이상 필요 없으면 표시 제거
    rec = json.loads(st.db.execute("SELECT record_json FROM notices WHERE id=?", (nid,)).fetchone()[0])
    rec["flags"]["clipped_fields"] = ["target_text"]                   # 예전 표시가 남은 상태
    st.db.execute("UPDATE notices SET record_json=? WHERE id=?", (json.dumps(rec, ensure_ascii=False), nid))
    run(st, item(지원내용=LONG_HEAD), 2)
    assert st.all_records()[0]["flags"]["clipped_fields"] == ["benefit_text"] and len(history(st)) == n


def test_same_source_reprocessed_does_not_repeat_history(tmp_path):
    st = Store(tmp_path / "t.db")
    run(st, item(), 0)
    run(st, item(수정일시="20261015090000"), 1)
    run(st, item(수정일시="20261015090000", 신청기한="2026. 10. 1. ~ 2026. 11. 15."), 2)
    n = len(history(st))
    for d in (3, 4, 5):
        assert run(st, item(수정일시="20261015090000", 신청기한="2026. 10. 1. ~ 2026. 11. 15."), d) == "same"
    assert len(history(st)) == n
    assert history(st)[:3] == [NOTE_CORE, NOTE_META, "최초 수집"]
