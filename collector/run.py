"""하루 1회 수집 실행.

- 배치 ID = 한국시간 날짜. 같은 날 다시 실행하면 이미 성공한 출처는 건너뛰고 실패한 출처만 재시도(최대 3회).
- 한 출처가 실패해도 다른 출처는 계속 수집.
- 연결 보류·미연결 출처는 실패와 구분해 기록하고, 수집 건수를 0이 아닌 null로 남긴다.
"""
from __future__ import annotations

import argparse
import os
import sys
import traceback
from pathlib import Path

from .export import export
from .manual import load_manual
from .models import now_kst
from .sources import build_sources
from .sources.base import SourceUnavailable, UnexpectedResponse
from .store import Store

MAX_ATTEMPTS = 3
ROOT = Path(__file__).resolve().parent.parent


def run_source(store: Store, src, batch_id: str, *, force: bool, log=print) -> str:
    """이번 실행에서 이 출처의 결과를 돌려준다(오늘 이전 시도·다른 실행의 결과와 구분)."""
    key = src.info.key
    prev = store.attempts(batch_id, key)
    if not force and any(r["status"] == "성공" for r in prev):
        log(f"[{key}] 오늘({batch_id}) 이미 성공 — 건너뜀")
        return "건너뜀(오늘 이미 성공)"
    if not force and sum(1 for r in prev if r["status"] == "실패") >= MAX_ATTEMPTS:
        log(f"[{key}] 오늘 {MAX_ATTEMPTS}회 실패 — 더 시도하지 않음")
        return "실패(오늘 재시도 한도)"
    since = store.last_success(key)
    attempt = store.start_run(batch_id, key, now_kst())
    try:
        result = src.fetch(since=since)
    except SourceUnavailable as e:
        store.finish_run(batch_id, key, attempt, now_kst(), status=e.status, error=str(e))
        log(f"[{key}] {e.status}: {e}")
        return e.status
    except UnexpectedResponse as e:
        store.finish_run(batch_id, key, attempt, now_kst(), status="실패", error=f"응답 구조 이상: {e}")
        log(f"[{key}] 실패(응답 구조): {e}")
        return "실패"
    except Exception as e:  # noqa: BLE001 — 한 출처의 오류가 전체를 멈추지 않게
        store.finish_run(batch_id, key, attempt, now_kst(), status="실패", error=f"{type(e).__name__}: {e}"[:500])
        log(f"[{key}] 실패: {e}")
        if os.getenv("DEBUG"):
            traceback.print_exc()
        return "실패"
    counts = {"new": 0, "changed": 0, "same": 0, "reclassified": 0, "meta_changed": 0}
    backfilled = unverified = 0
    now = now_kst()
    seen = set()
    for it in result.items:
        counts[store.upsert(it, batch_id, now)] += 1
        backfilled += store.last_backfilled
        unverified += store.last_unverified
        seen.add(it.source_id)
    store.mark_unseen(key, seen, batch_id)
    store.finish_run(batch_id, key, attempt, now_kst(), status="성공", fetched_raw=result.fetched_raw,
                     collected=len(result.items), new=counts["new"], changed=counts["changed"],
                     reclassified=counts["reclassified"], raw_backfilled=backfilled,
                     meta_changed=counts["meta_changed"], unverified=unverified, skipped=result.skipped,
                     fetch_mode=getattr(src, "mode", None))
    log(f"[{key}] 성공: 받은 {result.fetched_raw}건 중 대상 {len(result.items)}건 "
        f"(신규 {counts['new']}, 중요 원문 변경 {counts['changed']}(그중 변경 항목 확인 불가 {unverified}), "
        f"원본 메타데이터 변경 {counts['meta_changed']}, 분류 규칙 변경 {counts['reclassified']}, "
        f"원문 보완(데이터 이전) {backfilled})")
    return "성공"


class ManualSource:
    """manual/notices.json 을 출처처럼 처리."""

    def __init__(self, path):
        from .sources.base import SourceInfo
        self.path = path
        self.info = SourceInfo("manual", "수동 입력", ["공모전", "장학금", "생활비", "주거비"], "수동 입력",
                               "연결됨 · manual/notices.json")

    def fetch(self, *, since=None):
        from .sources.base import FetchResult
        items = load_manual(self.path)
        return FetchResult(items=items, fetched_raw=len(items))


def enabled_sources() -> set[str] | None:
    """ENABLED_SOURCES='gov24,youthcenter' 처럼 켤 출처 키를 쉼표로. 비어 있으면 전부 실행."""
    raw = (os.getenv("ENABLED_SOURCES") or "").strip()
    return {x.strip() for x in raw.split(",") if x.strip()} or None if raw else None


def load_dotenv(path: Path) -> None:
    """로컬 실행용 .env 읽기(이미 설정된 환경변수는 덮어쓰지 않음)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def main(argv=None) -> int:
    load_dotenv(ROOT / ".env")
    ap = argparse.ArgumentParser(prog="python -m collector")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="수집 실행 후 결과 파일 생성")
    r.add_argument("--db", default=os.getenv("NOTICE_DB", str(ROOT / "data" / "notices.db")))
    r.add_argument("--out", default=os.getenv("NOTICE_OUT", str(ROOT / "data" / "public")))
    r.add_argument("--only", nargs="*", help="이 출처 키만 실행 (예: gov24 youthcenter)")
    r.add_argument("--force", action="store_true", help="오늘 이미 성공한 출처도 다시 수집")
    r.add_argument("--batch", help="배치 ID(기본: 오늘 한국 날짜). 테스트용")
    e = sub.add_parser("export", help="수집 없이 결과 파일만 다시 생성")
    e.add_argument("--db", default=os.getenv("NOTICE_DB", str(ROOT / "data" / "notices.db")))
    e.add_argument("--out", default=os.getenv("NOTICE_OUT", str(ROOT / "data" / "public")))
    e.add_argument("--batch")
    v = sub.add_parser("youthcenter-review", help="보관한 온통청년 응답 스냅숏으로 판정만 다시 실행(API 호출 없음)")
    v.add_argument("--raw", required=True, help="youthcenter_raw.json.gz")
    v.add_argument("--out", required=True)
    v.add_argument("--today", help="신청 상태 기준일(YYYY-MM-DD)")
    v.add_argument("--before", help="비교할 수정 전 youthcenter_decisions.csv (선택)")
    args = ap.parse_args(argv)
    if args.cmd == "youthcenter-review":
        from .review_report import rereview
        rep = rereview(args.raw, args.out, args.today)
        print("보고서:", rep)
        if args.before:
            from .review_report import compare
            print("비교:", compare(args.before, Path(args.out) / "youthcenter_decisions.csv", Path(args.out) / "compare.md"))
        return 0

    batch_id = args.batch or now_kst().date().isoformat()
    store = Store(args.db)
    n_unv = store.migrate_unverified_backfill(now_kst())   # 1회성: 이미 했으면 0
    if n_unv:
        print(f"[데이터 점검] 원본 첫 보관 때 이전 요약값과 달랐던 공고 {n_unv}건을 '변경 항목 확인 불가'로 표시")
    sources = build_sources()
    manual_path = ROOT / "manual" / "notices.json"
    manual = ManualSource(manual_path) if manual_path.exists() else None
    try:
        outcomes: dict[str, str] = {}
        if args.cmd == "run":
            targets = sources + ([manual] if manual else [])
            enabled = enabled_sources()
            for src in targets:
                if args.only and src.info.key not in args.only:
                    continue
                if enabled is not None and src.info.key not in enabled:
                    # 꺼 둔 출처: 실패·0건과 구분해 '미실행'으로 기록
                    if not store.attempts(batch_id, src.info.key):
                        a = store.start_run(batch_id, src.info.key, now_kst())
                        store.finish_run(batch_id, src.info.key, a, now_kst(), status="미실행",
                                         error="이번 실행에서 꺼 둠 (ENABLED_SOURCES 설정)")
                    print(f"[{src.info.key}] 꺼 둠 — 실행하지 않음")
                    continue
                src.today = batch_id
                outcomes[src.info.key] = run_source(store, src, batch_id, force=args.force)
                if outcomes[src.info.key] == "성공" and getattr(src, "decisions", None):
                    # 판정 보고서·판정표·분류 검토 목록은 매 실행 남긴다(분류 검토는 DB에 넣지 않으므로 이 파일로 조회).
                    # 응답 원본 스냅숏은 시험 실행에서만(인증키 없음)
                    from .review_report import write_review
                    try:
                        write_review(src, Path(args.out) / "review", include_raw=os.getenv("DRY_RUN") == "1",
                                     secret=getattr(src, "key", None))
                    except Exception as e:  # noqa: BLE001 — 보고서 실패가 수집 결과를 바꾸지 않게
                        print(f"[{src.info.key}] 시험 보고서 작성 실패: {type(e).__name__}: {e}")
        data = export(store, sources, batch_id, args.out, include_manual=manual is not None,
                      outcomes=outcomes if args.cmd == "run" else None, dry_run=os.getenv("DRY_RUN") == "1")
    finally:
        store.close()
    print(f"배치 {batch_id}: {data['meta']['batch_status']} · 누적 {len(data['notices'])}건 → {args.out}")
    # 성공한 출처가 하나도 없으면 실패 종료 코드(워크플로가 빨간색으로 표시)
    return 1 if data["meta"]["batch_status"] == "실패" else 0


if __name__ == "__main__":
    sys.exit(main())
