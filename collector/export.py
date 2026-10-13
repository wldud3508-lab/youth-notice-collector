"""누적 DB → latest.json(화면용) + CSV 3종 + 실행 결과 요약."""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path

from .merge import merge_records
from .models import EXTRA_KEYS, now_kst, iso
from .store import Store

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema" / "notice-data.schema.json"
VERSION = "0.1.0"


from .rules import STATUS_ORDER, apply_status  # noqa: E402  (화면과 같은 상태 규칙)
from . import rules as R  # noqa: E402

# 대상·혜택·기간에 중요한 충돌이 있으면 '오늘 검토 후보'에서 뺀다
BLOCKING = {"apply_start", "apply_end", "age_condition", "amount_text", "target_text", "benefit_text", "중복 여부"}


def is_candidate(n: dict, today: str) -> bool:
    """오늘 검토할 후보: 청년 관련성 확인 + 신청기간이 명확한 접수중·신청예정 + 중요 충돌 없음 + 제외 아님."""
    if n.get("review_status") == "제외" or n.get("youth_relevant") is not True:
        return False
    if apply_status(n, today) not in (R.ST_OPEN, R.ST_UPCOMING) or not n.get("apply_end"):
        return False
    if any(c.get("field") in BLOCKING for c in n.get("conflicts") or []):
        return False
    return not (BLOCKING & set(n.get("needs_review_fields") or []))


def review_summary(notices: list[dict], today: str) -> dict:
    """'분류 성공'과 '정보 확인 완료'를 구분해 집계."""
    dated = [n for n in notices if n.get("apply_start") or n.get("apply_end")]
    undated = [n for n in notices if not (n.get("apply_start") or n.get("apply_end"))]
    unknown = [n for n in undated if n.get("period_kind") in (None, R.KIND_UNKNOWN)]
    out = {
        "날짜 확정": len(dated),
        "신청기간 확인 필요": sum(1 for n in undated if n.get("period_kind") in R.NEEDS_PERIOD_CHECK or n.get("period_kind") is None),
        "사유 분류됨": len(undated) - len(unknown),
        "사유 미분류": len(unknown),
        "필수 확인 항목 있음": sum(1 for n in notices if n.get("needs_review_fields") or n.get("conflicts")),
        "참고사항만 있음": sum(1 for n in notices if n.get("review_notes") and not (n.get("needs_review_fields") or n.get("conflicts"))),
        "오늘 검토 후보": sum(1 for n in notices if is_candidate(n, today)),
    }
    for k in R.PERIOD_KINDS:
        c = sum(1 for n in notices if n.get("period_kind") == k)
        if c:
            out["기간 유형: " + k] = c
    return out


def build_sources(store: Store, sources, batch_id: str, include_manual: bool) -> list[dict]:
    out = []
    infos = [s.info for s in sources]
    if include_manual:
        from .sources.base import SourceInfo
        infos.append(SourceInfo("manual", "수동 입력", ["공모전", "장학금", "생활비", "주거비"], "수동 입력",
                                "연결됨 · manual/notices.json"))
    for info in infos:
        r = store.latest_run(batch_id, info.key)
        ok = r is not None and r["status"] == "성공"
        status = r["status"] if r is not None else "미실행"
        if status == "실행 중":
            status = "실패"
        conn = info.connection
        if status in ("연결 보류", "미연결"):
            conn = f"{status} · {r['error']}" if r is not None and r["error"] else status
        out.append({
            "key": info.key, "name": info.name, "categories": info.categories, "connection": conn, "method": info.method,
            "run": {
                "status": status,
                "collected": r["collected"] if ok else None,
                "new": r["new"] if ok else None,
                "changed": r["changed"] if ok else None,
                "reclassified": r["reclassified"] if ok else None,
                "error": (r["error"] or None) if r is not None and not ok else None,
                "last_success_at": store.last_success(info.key),
                "attempts": len(store.attempts(batch_id, info.key)),
                "fetched_raw": r["fetched_raw"] if ok else None,
            },
        })
    return out


def status_counts(notices: list[dict], today: str) -> dict:
    """기준일의 신청 상태별 건수. 누적 건수와 별개로 '지금 신청 가능한 건수'를 보여 주기 위함."""
    c = {k: 0 for k in STATUS_ORDER}
    for n in notices:
        st = apply_status(n, today)
        c[st] = c.get(st, 0) + 1
    c["누적"] = len(notices)
    return c


def batch_status(srcs: list[dict]) -> str:
    st = [s["run"]["status"] for s in srcs]
    ok, fail = st.count("성공"), st.count("실패")
    if ok == 0:
        return "실패"
    return "부분 성공" if fail else "정상 완료"


def export(store: Store, sources, batch_id: str, out_dir: str | Path, *, include_manual: bool) -> dict:
    out = Path(out_dir)
    (out / "csv").mkdir(parents=True, exist_ok=True)
    notices = merge_records(store.all_records())
    srcs = build_sources(store, sources, batch_id, include_manual)
    data = {
        "meta": {
            "mode": "collected", "schema_version": "0.1", "batch_id": batch_id, "reference_date": batch_id,
            "generated_at": iso(now_kst()), "batch_status": batch_status(srcs),
            "collector_version": VERSION, "collect_trigger_url": os.getenv("COLLECT_TRIGGER_URL") or None,
        },
        "sources": srcs,
        "notices": notices,
    }
    data["meta"]["status_counts"] = status_counts(notices, batch_id)
    data["meta"]["review_summary"] = review_summary(notices, batch_id)
    validate(data)
    (out / "latest.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    today = batch_id
    is_new = lambda n: (n.get("first_seen_at") or "")[:10] == today
    is_chg = lambda n: not is_new(n) and (n.get("last_changed_at") or "")[:10] == today
    write_csv(out / "csv" / f"notices_all_{today}.csv", notices, today)
    write_csv(out / "csv" / f"notices_new_{today}.csv", [n for n in notices if is_new(n)], today)
    write_csv(out / "csv" / f"notices_changed_{today}.csv", [n for n in notices if is_chg(n)], today)
    (out / "summary.md").write_text(summary_md(data), encoding="utf-8")
    return data


def validate(data: dict):
    import jsonschema
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.validate(data, schema)


COLS = [
    ("내부 공고 ID", "id"), ("출처 사이트", lambda n, t: " | ".join(s["site"] for s in n["sources"])),
    ("출처 공고 ID", lambda n, t: " | ".join(s["source_id"] or "" for s in n["sources"])),
    ("분야", "category"), ("공고명", "title"), ("주최·운영기관", "organizer"), ("정보 유형", "info_type"),
    ("지원대상 원문", "target_text"),
    ("대학생·청년 관련", lambda n, t: None if n["youth_relevant"] is None else ("예" if n["youth_relevant"] else "아니오")),
    ("분류 근거", "youth_basis"), ("연령 조건", "age_condition"), ("지역", "region"), ("거주지역 조건", "region_condition"),
    ("소득·재산 조건", "income_condition"), ("재학·취업 상태 조건", "status_condition"), ("혜택 원문", "benefit_text"),
    ("지원방식", "support_type"), ("지원금액", "amount_text"), ("금액 수치", "amount_value"), ("금액 단위", "amount_unit"),
    ("금액 기간 단위", "amount_period"), ("금액 한정어", "amount_qualifier"),
    ("신청 시작일", "apply_start"), ("신청 마감일", "apply_end"), ("마감 시각", "apply_end_time"),
    ("신청기간 원문", "period_text"), ("신청 상태", lambda n, t: apply_status(n, t)), ("신청방법", "apply_method"),
    ("신청 링크", "apply_url"), ("필요서류", "required_docs"), ("제외조건·중복수혜 제한", "exclusions"),
    ("출처 게시물 링크", lambda n, t: " | ".join(s["url"] or "" for s in n["sources"])),
    ("주최기관 공식 링크", "official_url"), ("첨부 공고문 링크", "attachment_url"), ("게시일", "posted_at"),
    ("최초 수집 시각", "first_seen_at"), ("마지막 확인 시각", "last_checked_at"), ("마지막 변경 시각", "last_changed_at"),
    ("변경된 항목", lambda n, t: " | ".join(n["changed_fields"]) or None),
    ("확인 필요 항목", lambda n, t: " | ".join(n["needs_review_fields"]) or None),
    ("출처 간 충돌", lambda n, t: " | ".join(f"{c['field']}: " + " / ".join(f"{v['site']}={v['value']}" for v in c["values"]) for c in n["conflicts"]) or None),
    ("검토 상태(수집기 기본값)", "review_status"),
]
EXTRA_LABELS = {"contest_field": "공모 분야", "theme": "주제", "team_type": "개인·팀 참가", "submission": "제출물",
                "prize_text": "상금·시상 내역", "sch_kind": "등록금/생활비/기타", "pay_amount": "지급액",
                "pay_freq": "지급 횟수·기간", "grade_cond": "성적 조건", "year_cond": "학년 조건", "income_cond": "소득 조건",
                "dup_allowed": "중복수혜 가능 여부", "amount_period_text": "지원금액·기간", "budget_end": "예산 소진 시 종료",
                "household": "가구 기준", "housing": "주거 관련 조건", "self_pay": "본인 부담금", "loan_terms": "대출 금리·한도·상환"}
EXTRA_ORDER = list(dict.fromkeys(k for ks in EXTRA_KEYS.values() for k in ks))


def write_csv(path: Path, rows: list[dict], today: str):
    with path.open("w", encoding="utf-8-sig", newline="") as f:   # BOM: 엑셀 한글 깨짐 방지
        w = csv.writer(f)
        w.writerow(["데이터 구분"] + [c[0] for c in COLS] + ["[추가] " + EXTRA_LABELS[k] for k in EXTRA_ORDER])
        for n in rows:
            vals = ["수집 결과"]
            for _, k in COLS:
                v = k(n, today) if callable(k) else n.get(k)
                vals.append("" if v is None else v)
            vals += ["" if (n.get("extra") or {}).get(k) is None else n["extra"][k] for k in EXTRA_ORDER]
            w.writerow(vals)


def summary_md(data: dict) -> str:
    m = data["meta"]
    lines = [f"# 실행 결과 요약 — {m['batch_id']}", "", f"- 배치 상태: **{m['batch_status']}**",
             f"- 생성 시각: {m['generated_at']}", f"- 누적 공고: {len(data['notices'])}건 (마감 포함, 삭제하지 않고 보관)",
             f"- 기준일 {m['reference_date']} 신청 상태: " + " · ".join(f"{k} {m['status_counts'].get(k, 0)}건" for k in STATUS_ORDER),
             "- 확인 현황(분류 성공 ≠ 확인 완료): " + " · ".join(
                 f"{k} {v}건" for k, v in m.get("review_summary", {}).items() if not k.startswith("기간 유형")),
             "- 신청기간 유형: " + " · ".join(
                 f"{k[7:]} {v}건" for k, v in m.get("review_summary", {}).items() if k.startswith("기간 유형")),
             "", "| 출처 | 상태 | 수집 | 신규 | 공고 변경 | 분류 규칙 변경 | 오류 | 마지막 성공 |",
             "|---|---|---:|---:|---:|---:|---|---|"]
    for s in data["sources"]:
        r = s["run"]
        na = "집계 안 됨"
        f = lambda k: r.get(k) if r.get(k) is not None else na
        lines.append(f"| {s['name']} | {r['status']} | {f('collected')} | {f('new')} | {f('changed')} | {f('reclassified')} | "
                     f"{(r['error'] or '').replace('|', '/')} | {r['last_success_at'] or '없음'} |")
    if m["batch_status"] == "실패":
        lines += ["", "> 성공한 출처가 없습니다. 정상 완료가 아닙니다."]
    # 성공 여부를 초록색 표시만으로 판단하지 않도록, latest.json 에 실제로 들어간 공고를 출처별로 보여 준다
    lines += ["", "## latest.json 에 저장된 공고 (출처별 건수와 예시 3건)", ""]
    for s in data["sources"]:
        mine = [n for n in data["notices"] if any(x.get("source_key") == s["key"] for x in n["sources"])]
        if s["run"]["status"] != "성공" and not mine:
            continue
        lines.append(f"### {s['name']}: {len(mine)}건")
        for n in mine[:3]:
            url = next((x["url"] for x in n["sources"] if x.get("source_key") == s["key"]), None)
            lines.append(f"- [{n['category']}] {n['title']} · 신청기간 원문: {n['period_text'] or '미기재'} · "
                         f"출처 링크: {url or '없음'}")
        lines.append("")
    return "\n".join(lines) + "\n"
