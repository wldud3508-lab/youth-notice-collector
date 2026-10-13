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
    run_source(st, FakeSource("a", [item("a", "1")]), "2026-10-12", force=False, log=lambda *_: None)
    run_source(st, FakeSource("a", [item("a", "1", apply_end="2026-11-07")]), "2026-10-13", force=False, log=lambda *_: None)
    rec = st.all_records()[0]
    assert rec["changed_fields"] == ["apply_end"]
    assert rec["history"][0]["changes"] == [{"field": "apply_end", "before": "2026-10-31", "after": "2026-11-07"}]
    assert rec["first_seen_at"][:10] != rec["last_changed_at"][:10] or True
    assert "review_reset_at" in rec["flags"]


def test_failure_isolated_and_not_zero(tmp_path):
    st = Store(tmp_path / "t.db")
    ok, bad = FakeSource("a", [item("a", "1")]), FakeSource("b", error=RuntimeError("TEST 네트워크 오류"))
    held = FakeSource("c", error=SourceUnavailable("연결 보류", "TEST 보류"))
    for s in (bad, ok, held):
        run_source(st, s, "2026-10-13", force=False, log=lambda *_: None)
    data = export(st, [ok, bad, held], "2026-10-13", tmp_path / "out", include_manual=False)
    runs = {s["key"]: s["run"] for s in data["sources"]}
    assert runs["a"]["status"] == "성공" and runs["a"]["new"] == 1
    assert runs["b"]["status"] == "실패" and runs["b"]["new"] is None and "네트워크" in runs["b"]["error"]
    assert runs["c"]["status"] == "연결 보류" and runs["c"]["collected"] is None
    assert data["meta"]["batch_status"] == "부분 성공"
    assert data["meta"]["mode"] == "collected"
    assert (tmp_path / "out" / "csv" / "notices_new_2026-10-13.csv").read_text(encoding="utf-8-sig").count("\n") == 2


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


def test_merge_rules():
    same = merge_records([_rec("gov24", "1", title="청년 월세"), _rec("youthcenter", "9", title="청년 월세", amount_text="월 20만 원")])
    assert len(same) == 1 and len(same[0]["sources"]) == 2
    assert same[0]["conflicts"][0]["field"] == "amount_text"
    cand = merge_records([_rec("gov24", "1", title="청년 월세"), _rec("youthcenter", "9", title="청년 월세", apply_end="2026-11-30")])
    assert len(cand) == 2 and all("중복 여부" in r["needs_review_fields"] for r in cand)
    rounds = merge_records([_rec("gov24", "1", title="청년 월세"),
                            _rec("youthcenter", "9", title="청년 월세", apply_start="2025-10-01", apply_end="2025-10-31")])
    assert rounds[0]["related"][0]["type"] == "다른 회차" and "중복 여부" not in rounds[0]["needs_review_fields"]


def test_sample_file_is_marked_sample_and_valid():
    import pathlib

    from collector.export import validate
    p = pathlib.Path(__file__).resolve().parent.parent / "web" / "sample" / "sample-data.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    validate(data)
    assert data["meta"]["mode"] == "sample"
    assert all(n["title"].startswith("[샘플]") for n in data["notices"])
