"""누적 DB → latest.json(화면용) + CSV 3종 + 실행 결과 요약."""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path

from .merge import CROSS, cross_source_pairs, link_records
from .models import EXTRA_KEYS, now_kst, iso
from .store import Store

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema" / "notice-data.schema.json"
VERSION = "0.1.0"


from .rules import STATUS_ORDER, apply_status  # noqa: E402  (화면과 같은 상태 규칙)
from . import rules as R  # noqa: E402

# 대상·혜택·기간에 중요한 충돌이 있으면 '오늘 검토 후보'에서 뺀다
BLOCKING = {"apply_start", "apply_end", "age_condition", "amount_text", "target_text", "benefit_text", "중복 여부", "원본 변경 확인",
            "공식 안내 링크 확인 필요"}


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
    sources_by_key = {s.info.key: s for s in sources}
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
        # 실제 출처에서 자동으로 받아 성공한 기록이 있어야 '실제 연결 성공'. 시험용 CSV 실행은 해당하지 않음
        stage = "실제 연결 성공" if store.last_success(info.key, real_only=True) else info.stage
        mode = r["fetch_mode"] if r is not None else None
        out.append({
            "key": info.key, "name": info.name, "categories": info.categories, "connection": conn, "method": info.method,
            "stage": stage, "stage_note": info.stage_note if stage != "실제 연결 성공" else None,
            "source_version": getattr(sources_by_key.get(info.key), "source_version", None),
            "fetch_mode": mode,
            "run": {
                "status": status,
                "collected": r["collected"] if ok else None,
                "new": r["new"] if ok else None,
                "changed": r["changed"] if ok else None,
                "reclassified": r["reclassified"] if ok else None,
                "raw_backfilled": r["raw_backfilled"] if ok else None,
                "meta_changed": r["meta_changed"] if ok else None,
                "unverified": r["unverified"] if ok else None,
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


def batch_status(srcs: list[dict], outcomes: dict | None = None) -> str:
    if outcomes:   # 이번 실행에서 선택한 출처의 결과만으로 판단(과거 성공 기록은 참고 정보)
        ok_like = [v for v in outcomes.values() if v in ("성공", "건너뜀(오늘 이미 성공)")]
        fail = [v for v in outcomes.values() if v.startswith("실패")]
        if not ok_like:
            return "실패"
        return "부분 성공" if fail else "정상 완료"
    st = [s["run"]["status"] for s in srcs]
    ok, fail = st.count("성공"), st.count("실패")
    if ok == 0:
        return "실패"
    return "부분 성공" if fail else "정상 완료"


def export(store: Store, sources, batch_id: str, out_dir: str | Path, *, include_manual: bool,
           outcomes: dict | None = None, dry_run: bool = False) -> dict:
    out = Path(out_dir)
    (out / "csv").mkdir(parents=True, exist_ok=True)
    notices = link_records(store.all_records())   # 출처가 다르면 합치지 않고 후보로 연결만
    srcs = build_sources(store, sources, batch_id, include_manual)
    for s_ in srcs:   # 이번 실행에서 실제로 돌린 결과(없으면 이번 실행 대상 아님)
        s_["run"]["this_run"] = (outcomes or {}).get(s_["key"]) if outcomes is not None else None
    data = {
        "meta": {
            "mode": "collected", "schema_version": "0.1", "batch_id": batch_id, "reference_date": batch_id,
            "generated_at": iso(now_kst()), "batch_status": batch_status(srcs, outcomes),
            "run_scope": {"dry_run": dry_run, "selected": sorted(outcomes) if outcomes else None,
                          "outcomes": outcomes or None},
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
    extra = []
    rep = out / "review" / "youthcenter_report.md"
    if dry_run and outcomes and outcomes.get("youthcenter") == "성공" and rep.exists():
        extra = ["", rep.read_text(encoding="utf-8")]
    elif outcomes and outcomes.get("youthcenter") == "성공" and (out / "review" / "youthcenter_review_needed.csv").exists():
        # 정식 실행: 분류 검토 목록이 DB에 없다는 점과 위치만 요약에 남긴다
        with (out / "review" / "youthcenter_review_needed.csv").open(encoding="utf-8-sig", newline="") as f:
            n = sum(1 for _ in csv.reader(f)) - 1
        extra = ["", f"- 온통청년 분류 검토 {n}건: 미확정·DB 미반영(제외 아님, 검토 완료 아님). "
                     "data 브랜치 public/review/youthcenter_review_needed.csv 에서 확인"]
    (out / "summary.md").write_text(summary_md(data) + "\n".join(extra), encoding="utf-8")
    if dry_run and outcomes:
        # 시험 실행은 화면에 배포하지 않으므로, 이번 실행 출처의 신규 목록을 Artifacts 로 올라가는 폴더에 따로 둔다
        (out / "review").mkdir(exist_ok=True)
        write_csv(out / "review" / "new_this_run.csv",
                  [n for n in notices if is_new(n) and n["sources"][0]["source_key"] in outcomes], today)
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


def _short(v, n: int = 160) -> str:
    s = "없음" if v is None else " ".join(str(v).split())
    return s if len(s) <= n else s[:n] + f" …({len(s)}자 중 {n}자)"


def links_text(n: dict) -> str:
    """링크 종류를 구분해 보여 준다(다른 출처의 링크로 대체하지 않음)."""
    out = [f"{s['site']} 상세 {s['url']}" for s in n.get("sources") or [] if s.get("url")]
    fl = n.get("flags") or {}
    if fl.get("links"):
        out += [f"{x['type']} {x['url']}" for x in fl["links"]]
    else:
        out += [f"신청 링크 {n['apply_url']}"] if n.get("apply_url") else []
        out += [f"주최기관 공식 {n['official_url']}"] if n.get("official_url") else []
    return " · ".join(out) if out else "없음 (공식 안내 링크 확인 필요)"


def todays_findings(data: dict) -> list[str]:
    """오늘 실제로 발견한 신규·변경을 일반 예시와 따로 보여 준다. 원본 변경은 '모집 시작'을 뜻하지 않는다."""
    m, today = data["meta"], data["meta"]["batch_id"]
    on_today = lambda h: (h.get("batch") == today)
    new, core, meta, unk = [], [], [], []
    periodic = {}   # 월간 파일 등 정기 자료: 공고 단위가 아니라 자료 반영으로 따로 집계
    for n in data["notices"]:
        cad = (n.get("flags") or {}).get("source_cadence")
        if cad:
            for h in n.get("history") or []:
                if on_today(h) and h["note"] in ("최초 수집", "중요 원문 변경", "원본 변경 — 변경 항목 확인 불가"):
                    key = (n["sources"][0]["site"], cad, (n.get("flags") or {}).get("source_version"))
                    periodic.setdefault(key, {"최초 수집": 0, "변경": 0})["최초 수집" if h["note"] == "최초 수집" else "변경"] += 1
            continue
        for h in n.get("history") or []:
            if not on_today(h):
                continue
            {"최초 수집": new, "중요 원문 변경": core, "원본 메타데이터 변경": meta,
             "원본 변경 — 변경 항목 확인 불가": unk}.get(h["note"], []).append((n, h))
    url = links_text
    out = ["", f"## 오늘 발견 ({today})", "",
           "> 신규는 '이 수집 목록에 처음 들어온 공고'이고, 원문 변경은 '출처의 글이 바뀐 것'입니다. "
           "어느 쪽도 그 자체로 모집이 새로 시작됐다는 뜻은 아닙니다. 게시·등록일과 신청기간 원문을 함께 보세요.", ""]
    scope = m.get("run_scope") or {}
    sel = set(scope.get("selected") or [])
    if sel:   # 이번 실행에서 돌린 출처의 신규와, 오늘 다른 실행(예: 오전 정식 실행)의 신규를 나눈다
        this = [(n, h) for n, h in new if n["sources"][0]["source_key"] in sel]
        other = len(new) - len(this)
        out.append(f"### 신규 — 이번 실행({', '.join(sorted(sel))}) {len(this)}건 · 오늘 전체 {len(new)}건"
                   + (f" (이번 실행 대상이 아닌 출처 {other}건 포함)" if other else ""))
        new = this + [x for x in new if x not in this]
    else:
        out.append(f"### 신규 {len(new)}건")
    if len(new) > 15:
        if scope.get("dry_run"):
            out.append(f"- 15건만 표시. 시험 실행이라 관리 화면에는 배포되지 않았습니다. 나머지 {len(new) - 15}건은 이 실행의 "
                       "Artifacts(trial-review-…)에 있는 review/new_this_run.csv 에서 확인하세요")
        else:
            out.append(f"- (처음 연결한 출처 등으로 많아서 15건만 표시, 나머지 {len(new) - 15}건은 관리 화면 '오늘 신규'에서 확인)")
    for n, h in new[:15]:
        out += [f"- **{n['title']}** ({n.get('organizer') or '기관 미기재'}) · [{n['category']}] {n.get('region') or '지역 미기재'}",
                f"  - 지원대상: {_short(n.get('target_text'))}",
                f"  - 혜택: {_short(n.get('benefit_text'))}",
                f"  - 신청기간 원문: {n.get('period_text') or '미기재'} → 현재 상태: {apply_status(n, today)}"
                f" (신청기간 유형: {n.get('period_kind') or '미분류'})",
                f"  - 출처 게시·등록일: {n.get('posted_at') or '미기재'} · 이 수집에 처음 들어온 시각: {n.get('first_seen_at')}",
                f"  - 링크: {url(n)}"]
    out += ["", f"### 중요 원문 변경 {len(core)}건 (검토 상태를 미검토로 되돌림)"]
    for n, h in core:
        out.append(f"- **{n['title']}** · 감지 시각 {h['at']} (실제 변경 시각 아님) · 현재 상태: {apply_status(n, today)} · {url(n)}")
        for c in h.get("changes") or []:
            out.append(f"  - {c['field']}: {_short(c.get('before'))} → {_short(c.get('after'))}")
    if meta:
        out += ["", f"### 원본 메타데이터 변경 {len(meta)}건 (검토 상태 유지)"]
        out += [f"- {n['title']}: " + ", ".join(c["field"] for c in h.get("changes") or []) for n, h in meta]
    if unk:
        out += ["", f"### 원본 변경 — 변경 항목 확인 불가 {len(unk)}건 (원문 직접 확인 필요)"]
        out += [f"- {n['title']} · {url(n)}" for n, h in unk]
    if periodic:
        out += ["", "### 정기 자료 반영 (매일 올라오는 모집공고가 아님)"]
        for (site, cad, ver), c in periodic.items():
            out.append(f"- {site} {cad} · 원본 기준일 {ver or '확인 못 함'} · 우리 수집일 {today}: "
                       f"처음 반영 {c['최초 수집']}건, 내용 바뀐 항목 {c['변경']}건. 모집 상태는 각 항목의 모집시작일·종료일로 판단")
    return out


def summary_md(data: dict) -> str:
    m = data["meta"]
    scope = m.get("run_scope") or {}
    head = ["- 실행 종류: 시험 실행(누적 데이터 사본, 저장·배포 안 함)", ""] if scope.get("dry_run") else []
    sel = ", ".join(f"{k}: {v}" for k, v in (scope.get("outcomes") or {}).items()) or "없음"
    lines = [f"# 실행 결과 요약 — {m['batch_id']}", ""] + head + [
             f"- 이번 실행 결과: **{m['batch_status']}** (이번에 돌린 출처만으로 판단 — {sel})",
             "- 이번에 돌리지 않은 출처의 '오늘 마지막 기록'은 참고 정보이며 이번 실행 결과에 포함되지 않습니다.",
             f"- 생성 시각: {m['generated_at']}", f"- 누적 공고: {len(data['notices'])}건 (마감 포함, 삭제하지 않고 보관)",
             f"- 기준일 {m['reference_date']} 신청 상태: " + " · ".join(f"{k} {m['status_counts'].get(k, 0)}건" for k in STATUS_ORDER),
             "- 확인 현황(분류 성공 ≠ 확인 완료): " + " · ".join(
                 f"{k} {v}건" for k, v in m.get("review_summary", {}).items() if not k.startswith("기간 유형")),
             "- 신청기간 유형: " + " · ".join(
                 f"{k[7:]} {v}건" for k, v in m.get("review_summary", {}).items() if k.startswith("기간 유형")),
             "", "| 출처 | 이번 실행 결과 | 오늘 마지막 기록(참고) | 수집 | 신규 | 중요 원문 변경 | 그중 변경 항목 확인 불가 | 원본 메타데이터 변경 | 분류 규칙 변경 | 원문 보완(데이터 이전) | 오류 | 마지막 성공 |",
             "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|"]
    for s in data["sources"]:
        r = s["run"]
        na = "집계 안 됨"
        f = lambda k: r.get(k) if r.get(k) is not None else na
        this = r.get("this_run") or "이번 실행 대상 아님"
        lines.append(f"| {s['name']} | {this} | {r['status']} | {f('collected')} | {f('new')} | {f('changed')} | {f('unverified')} | {f('meta_changed')} | {f('reclassified')} | {f('raw_backfilled')} | "
                     f"{(r['error'] or '').replace('|', '/')} | {r['last_success_at'] or '없음'} |")
    if m["batch_status"] == "실패":
        lines += ["", "> 성공한 출처가 없습니다. 정상 완료가 아닙니다."]
    stored = {}
    for n in data["notices"]:
        k = n["sources"][0]["source_key"]
        stored[k] = stored.get(k, 0) + 1
    cross = cross_source_pairs(data["notices"])
    names = {s["key"]: s["name"] for s in data["sources"]}
    lines += ["", "- 출처별 저장 기록 수(출처가 다르면 따로 저장, 합치지 않음): "
              + " · ".join(f"{names.get(k, k)} {v}건" for k, v in stored.items()),
              f"- 다른 출처와 '{CROSS}'로 연결된 기록: "
              + (" · ".join(f"{names.get(k, k)} {v}건" for k, v in cross.items()) if cross else "없음")
              + " (공고명 정규화 일치 기준, 내용은 각 출처 원문 그대로)"]
    lines += todays_findings(data)
    # 성공 여부를 초록색 표시만으로 판단하지 않도록, latest.json 에 실제로 들어간 공고를 출처별로 보여 준다
    lines += ["", "## 일반 공고 예시 (오늘 발견과 무관, 출처별 누적 건수와 앞 3건)", ""]
    for s in data["sources"]:
        mine = [n for n in data["notices"] if any(x.get("source_key") == s["key"] for x in n["sources"])]
        if s["run"]["status"] != "성공" and not mine:
            continue
        lines.append(f"### {s['name']}: {len(mine)}건")
        for n in mine[:3]:
            lines.append(f"- [{n['category']}] {n['title']} · 신청기간 원문: {n['period_text'] or '미기재'} · "
                         f"링크: {links_text(n)}")
        lines.append("")
    return "\n".join(lines) + "\n"
