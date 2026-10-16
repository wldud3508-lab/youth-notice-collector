"""저장·중복·변경·실패 처리·내보내기 통합 테스트 (가짜 출처 사용)."""
import json

from collector.export import export
from collector.merge import merge_records
from collector.models import Item
from collector.run import run_source
from collector.sources.base import FetchResult, SourceInfo, SourceUnavailable
from collector.store import Store


class FakeSource:
    def __init__(self, key, items=None, error=None):
        self.info = SourceInfo(key, f"TEST-{key}", ["생활비"], "테스트", "테스트 연결")
        self.items, self.error, self.calls = items or [], error, 0

    def fetch(self, *, since=None):
        self.calls += 1
        if self.error:
            raise self.error
        return FetchResult(items=list(self.items), fetched_raw=len(self.items))


def item(key, sid, **f):
    base = {"category": "생활비", "title": f"[TEST] 공고 {sid}", "organizer": "TEST기관", "info_type": "모집공고",
            "apply_start": "2026-10-01", "apply_end": "2026-10-31", "amount_text": "월 10만 원"}
    base.update(f)
    return Item(key, f"TEST-{key}", sid, f"https://example.com/{key}/{sid}", None, base)


def test_rerun_same_day_no_duplicates_and_skip(tmp_path):
    st = Store(tmp_path / "t.db")
    src = FakeSource("a", [item("a", "1"), item("a", "2")])
    run_source(st, src, "2026-10-13", force=False, log=lambda *_: None)
    run_source(st, src, "2026-10-13", force=False, log=lambda *_: None)
    assert src.calls == 1                       # 이미 성공 → 재수집 안 함
    run_source(st, src, "2026-10-13", force=True, log=lambda *_: None)
    assert len(st.all_records()) == 2           # 강제 재수집해도 중복 저장 없음


def test_change_detection_records_history(tmp_path):
    st = Store(tmp_path / "t.db")
    run_source(st, FakeSource("a", [item("a", "1", period_text="10. 1. ~ 10. 31.")]), "2026-10-12", force=False, log=lambda *_: None)
    run_source(st, FakeSource("a", [item("a", "1", period_text="10. 1. ~ 11. 7.", apply_end="2026-11-07")]), "2026-10-13",
               force=False, log=lambda *_: None)
    rec = st.all_records()[0]
    assert set(rec["changed_fields"]) == {"apply_end", "period_text"}
    assert {"field": "apply_end", "before": "2026-10-31", "after": "2026-11-07"} in rec["history"][0]["changes"]
    assert rec["history"][0]["note"] == "공고 내용 변경"
    assert "review_reset_at" in rec["flags"]


def test_rule_only_change_is_not_official_change(tmp_path):
    """원문(period_text)은 같고 분류 결과만 달라지면 '분류 규칙 변경' — 공고 변경으로 표시하지 않음."""
    st = Store(tmp_path / "t.db")
    src = lambda **f: FakeSource("a", [item("a", "1", period_text="매년 3~4월", apply_start=None, apply_end=None, **f)])
    run_source(st, src(), "2026-10-12", force=False, log=lambda *_: None)
    run_source(st, src(period_kind="매년·정기 반복"), "2026-10-13", force=False, log=lambda *_: None)
    rec = st.all_records()[0]
    assert rec["period_kind"] == "매년·정기 반복"
    assert rec["last_changed_at"] is None and rec["changed_fields"] == []
    assert "review_reset_at" not in rec["flags"] and "rule_changed_at" in rec["flags"]
    assert rec["history"][0]["note"] == "분류 규칙 변경"
    run = st.latest_run("2026-10-13", "a")
    assert (run["changed"], run["reclassified"]) == (0, 1)


def test_source_digest_decides_official_change(tmp_path):
    st = Store(tmp_path / "t.db")
    mk = lambda d, **f: FakeSource("a", [Item("a", "TEST-a", "1", None, None, {"category": "생활비", "title": "T", **f}, source_digest=d)])
    run_source(st, mk("d1"), "2026-10-11", force=False, log=lambda *_: None)
    run_source(st, mk("d1", period_kind="상시"), "2026-10-12", force=False, log=lambda *_: None)   # 원본 같음
    assert st.latest_run("2026-10-12", "a")["reclassified"] == 1
    run_source(st, mk("d2", period_kind="상시", benefit_text="변경"), "2026-10-13", force=False, log=lambda *_: None)
    assert st.latest_run("2026-10-13", "a")["changed"] == 1


def test_failure_isolated_and_not_zero(tmp_path):
    st = Store(tmp_path / "t.db")
    ok, bad = FakeSource("a", [item("a", "1")]), FakeSource("b", error=RuntimeError("TEST 네트워크 오류"))
    held = FakeSource("c", error=SourceUnavailable("연결 보류", "TEST 보류"))
    today = __import__("collector.models", fromlist=["now_kst"]).now_kst().date().isoformat()   # 신규 판정은 실제 수집 날짜 기준
    for s in (bad, ok, held):
        run_source(st, s, today, force=False, log=lambda *_: None)
    data = export(st, [ok, bad, held], today, tmp_path / "out", include_manual=False)
    runs = {s["key"]: s["run"] for s in data["sources"]}
    assert runs["a"]["status"] == "성공" and runs["a"]["new"] == 1
    assert runs["b"]["status"] == "실패" and runs["b"]["new"] is None and "네트워크" in runs["b"]["error"]
    assert runs["c"]["status"] == "연결 보류" and runs["c"]["collected"] is None
    assert data["meta"]["batch_status"] == "부분 성공"
    assert data["meta"]["mode"] == "collected"
    assert (tmp_path / "out" / "csv" / f"notices_new_{today}.csv").read_text(encoding="utf-8-sig").count("\n") == 2


def test_failed_source_retried_at_most_three_times(tmp_path):
    st = Store(tmp_path / "t.db")
    bad = FakeSource("b", error=RuntimeError("x"))
    for _ in range(5):
        run_source(st, bad, "2026-10-13", force=False, log=lambda *_: None)
    assert bad.calls == 3


def test_all_failed_is_not_success(tmp_path):
    st = Store(tmp_path / "t.db")
    bad = FakeSource("b", error=RuntimeError("x"))
    run_source(st, bad, "2026-10-13", force=False, log=lambda *_: None)
    data = export(st, [bad], "2026-10-13", tmp_path / "out", include_manual=False)
    assert data["meta"]["batch_status"] == "실패"
    assert "정상 완료가 아닙니다" in (tmp_path / "out" / "summary.md").read_text(encoding="utf-8")


def test_unseen_notice_is_kept_not_closed(tmp_path):
    st = Store(tmp_path / "t.db")
    run_source(st, FakeSource("a", [item("a", "1"), item("a", "2")]), "2026-10-12", force=False, log=lambda *_: None)
    run_source(st, FakeSource("a", [item("a", "1")]), "2026-10-13", force=False, log=lambda *_: None)
    recs = {r["sources"][0]["source_id"]: r for r in st.all_records()}
    assert recs["2"]["flags"]["not_seen_since"] == "2026-10-13"
    assert recs["2"]["apply_status_override"] is None


def _rec(key, sid, **f):
    r = item(key, sid, **f).to_record()
    r.update(first_seen_at="2026-10-13T09:00:00+09:00", last_checked_at="2026-10-13T09:00:00+09:00")
    return r


def test_cross_source_never_merged_only_linked():
    gov = _rec("gov24", "1", title="청년 월세", amount_text="월 20만 원", apply_url="https://gov.example/TEST")
    gov.update(review_status="확인 완료", needs_review_fields=[])
    yc = _rec("youthcenter", "9", title="청년 월세", amount_text="월 30만 원", apply_url=None)
    out = merge_records([gov, yc])
    assert len(out) == 2                                     # 출처가 다르면 따로 남음
    g, y = (next(r for r in out if r["sources"][0]["source_key"] == k) for k in ("gov24", "youthcenter"))
    assert len(g["sources"]) == 1 and len(y["sources"]) == 1
    assert g["amount_text"] == "월 20만 원" and y["amount_text"] == "월 30만 원"   # 서로 덮어쓰지 않음
    assert y["apply_url"] is None                             # 다른 출처 링크로 채우지 않음
    assert g["related"][0]["type"] == "같은 정책 후보(다른 출처)" and g["related"][0]["id"] == y["id"]
    assert g["review_status"] == "확인 완료" and g["needs_review_fields"] == []   # 정부24 검토 상태 보존
    assert not g["conflicts"]
    # 같은 출처끼리는 연결하지 않음, 제목이 다르면 연결 없음
    assert not merge_records([_rec("gov24", "1", title="A"), _rec("gov24", "2", title="A")])[0]["related"]
    assert not merge_records([_rec("gov24", "1", title="A"), _rec("youthcenter", "2", title="B")])[0]["related"]


def test_sample_file_is_marked_sample_and_valid():
    import pathlib

    from collector.export import validate
    p = pathlib.Path(__file__).resolve().parent.parent / "web" / "sample" / "sample-data.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    validate(data)
    assert data["meta"]["mode"] == "sample"
    assert all(n["title"].startswith("[샘플]") for n in data["notices"])


def test_full_raw_kept_and_display_clipped(tmp_path):
    """화면용 필드는 600자에서 줄이되, 원본 응답 전체는 DB에 보관하고 금액 판정도 전체 원문으로 한다."""
    from collector.sources.gov24 import Gov24
    from tests.fakes import gov24_http, gov24_row
    long = "가" * 700 + " 월 최대 30만 원 지급"      # 금액이 600자 뒤에 있음
    it = Gov24(service_key="K", http=gov24_http([gov24_row(지원내용=long)])).fetch().items[0]
    st = Store(tmp_path / "t.db")
    st.upsert(it, "2026-10-13", __import__("collector.models", fromlist=["now_kst"]).now_kst())
    rec = st.all_records()[0]
    assert len(rec["benefit_text"]) < len(long) and "benefit_text" in rec["flags"]["clipped_fields"]
    assert rec["amount_text"] == "월 최대 30만 원"                     # 잘린 부분의 금액도 찾음
    assert st.raw_of(rec["id"])["list"]["지원내용"] == long             # 원문 전체 보관
    assert "조회수" not in st.raw_of(rec["id"])["list"]


def test_first_raw_storage_is_migration_not_official_change(tmp_path):
    """기존 행(원본 요약값은 있으나 원본 미보관)에 원본을 처음 보관 → '원문 보완(데이터 이전)', 공고 변경 아님."""
    from collector.models import now_kst
    st = Store(tmp_path / "t.db")
    base = {"category": "생활비", "title": "T", "benefit_text": "월 10만 원"}
    st.upsert(Item("a", "TEST-a", "1", None, None, base, source_digest="d1"), "2026-10-13", now_kst())   # 옛 방식: 원본 없음
    r = st.upsert(Item("a", "TEST-a", "1", None, None, base, source_digest="d1", raw={"TEST": 1}), "2026-10-14", now_kst())
    rec = st.all_records()[0]
    assert r == "same" and st.last_backfilled
    assert rec["last_changed_at"] is None and "review_reset_at" not in rec["flags"]
    assert rec["history"][0]["note"] == "원문 보완(데이터 이전)"
    # 다음 날 같은 원본 → 아무 기록도 추가되지 않음
    r = st.upsert(Item("a", "TEST-a", "1", None, None, base, source_digest="d1", raw={"TEST": 1}), "2026-10-15", now_kst())
    assert r == "same" and not st.last_backfilled and len(st.all_records()[0]["history"]) == 2


def test_digest_method_change_is_migration(tmp_path):
    from collector.models import now_kst
    st = Store(tmp_path / "t.db")
    base = {"category": "생활비", "title": "T", "benefit_text": "월 10만 원"}
    st.upsert(Item("a", "TEST-a", "1", None, None, base, source_digest="d1", raw={"v": 1}), "2026-10-13", now_kst())
    r = st.upsert(Item("a", "TEST-a", "1", None, None, {**base, "period_kind": "상시"}, source_digest="NEW",
                       raw={"v": 1}, digest_version=2), "2026-10-14", now_kst())
    rec = st.all_records()[0]
    assert r == "reclassified" and rec["last_changed_at"] is None
    assert rec["history"][0]["note"] == "원문 보완(데이터 이전) · 분류 규칙 변경"
    # 데이터 이전 중이라도 화면용 원문 필드가 바뀌면 공고 변경으로 기록
    r = st.upsert(Item("a", "TEST-a", "1", None, None, {**base, "benefit_text": "월 20만 원"}, source_digest="NEW2",
                       raw={"v": 2}, digest_version=3), "2026-10-15", now_kst())
    assert r == "changed"
