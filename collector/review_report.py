"""온통청년 시험 보고서: 실제 응답의 분류값 분포, 판정(포함/분류 검토/제외)별 건수·사유, 예시.

- 시험 실행(DRY_RUN=1)에서만 원본 스냅숏(youthcenter_raw.json.gz)을 남긴다. 인증키·요청 주소는 넣지 않으며,
  쓰기 전에 인증키 문자열이 들어 있지 않은지 검사한다(들어 있으면 쓰지 않고 실패).
- 스냅숏으로 API를 다시 부르지 않고 같은 판정을 재실행할 수 있다:
    python -m collector youthcenter-review --raw youthcenter_raw.json.gz --out 결과폴더
"""
from __future__ import annotations

import csv
import gzip
import json
from collections import Counter
from pathlib import Path

from . import rules as R
from . import textutil as T
from .sources.youthcenter_rules import EXCLUDE, INCLUDE, REVIEW

# 지난 시험(2026-10-16 19:00)에서 잘못 분류됐다고 지적된 정책. 이번 판정 결과를 따로 보여 준다(판정에는 쓰지 않음).
WATCHLIST = ["청년톡톡콘서트", "임신부 가사지원", "난임부부 진단검사비", "청년 일경험", "근로인센티브", "천원의 아침밥",
             "면접정장대여", "입영지원금", "학자금대출 이자", "청년쉐어하우스", "인천시 청년월세", "전월세 보증금 대출이자",
             # 2026-10-16 두 번째 시험에서 지적된 예시
             "청년 관광두레", "취업 후 상환 학자금대출", "청년안심주택"]
EXAMPLES_PER = 3
REVIEW_STATUS = "미확정 · DB 미반영(제외 아님, 검토 완료 아님)"


def _counter_lines(c: Counter, top: int | None = None) -> list[str]:
    items = c.most_common(top)
    return [f"| {k or '(빈 값)'} | {v} |" for k, v in items]


def _split_kw(v) -> list[str]:
    s = T.clean(v)
    return [x.strip() for x in s.split(",") if x.strip()] if s else ["(빈 값)"]


def build_report(src) -> str:
    rows, decs = src.rows, src.decisions
    total, pages = getattr(src, "total", None), getattr(src, "pages", None)
    ids = [T.clean(r.get("plcyNo")) for r in rows]
    uniq = {i for i in ids if i}
    dup_rows = sum(n - 1 for n in src.dup_ids.values())
    same_title = Counter((T.norm_key(r.get("plcyNm")), T.norm_key(r.get("sprvsnInstCdNm") or r.get("operInstCdNm"))) for r in rows
                         if T.clean(r.get("plcyNo")))
    same_title_groups = {k: v for k, v in same_title.items() if k[0] and v > 1}
    verdict = Counter(d.verdict for _, d, _ in decs)
    L = [f"## 온통청년 시험 보고서", "",
         "### 1. 응답 수신", "",
         f"- API 전체 건수(totCount): {total} · 받은 행: {len(rows)} · 페이지: {pages}쪽"
         + (" · **전체 수신 확인**" if total is not None and len(rows) == total else " · **전체 수신 아님**"),
         f"- 고유 정책번호(plcyNo): {len(uniq)}개 · 같은 정책번호 중복 행: {dup_rows}행({len(src.dup_ids)}개 번호)",
         f"- 같은 출처 안에서 정책명·주관기관이 같은데 정책번호가 다른 묶음: {len(same_title_groups)}개 "
         f"({sum(same_title_groups.values())}행) — 자동으로 합치지 않음, 참고용",
         f"- 판정: 포함 {verdict.get(INCLUDE, 0)} · 분류 검토 {verdict.get(REVIEW, 0)} · 제외 {verdict.get(EXCLUDE, 0)} "
         f"(합계 {sum(verdict.values())}, 중복 행 제외)", "",
         f"> **분류 검토 {verdict.get(REVIEW, 0)}건은 미확정·DB 미반영 대상입니다.** '제외'도 '검토 완료'도 아니며, "
         "관리 화면과 누적 DB(notices.db)·latest.json 에는 들어가지 않습니다. "
         "목록은 같은 폴더의 `youthcenter_review_needed.csv`(분류 검토만)와 `youthcenter_decisions.csv`(전체 판정)에서 "
         "계속 확인할 수 있습니다. DB에 들어가는 것은 '포함' 판정뿐입니다.", ""]

    L += ["### 2. 분야별 판정", "", "| 분야 | 포함 | 분류 검토(잠정 분야) |", "|---|---:|---:|"]
    for cat in ("장학금", "생활비", "주거비", None):
        inc = sum(1 for _, d, _ in decs if d.verdict == INCLUDE and d.category == cat)
        rev = sum(1 for _, d, _ in decs if d.verdict == REVIEW and d.category == cat)
        if inc or rev or cat:
            L.append(f"| {cat or '잠정 분야 없음'} | {inc} | {rev} |")
    aid = Counter(d.aid_kind for _, d, _ in decs if d.verdict == INCLUDE and d.category == "장학금")
    if aid:
        L += ["", "장학금 분야 지원유형(대출·이자지원·채무조정은 무상 장학금 아님): "
              + " · ".join(f"{k} {v}" for k, v in aid.most_common())]
    L += ["", "**분류 검토 사유**", "", "| 사유 | 건수 |", "|---|---:|"]
    L += _counter_lines(Counter(d.reason for _, d, _ in decs if d.verdict == REVIEW))
    L += ["", "**제외 사유**", "", "| 사유 | 건수 |", "|---|---:|"]
    L += _counter_lines(Counter(d.reason for _, d, _ in decs if d.verdict == EXCLUDE))

    inc_items = [it for _, d, it in decs if d.verdict == INCLUDE and it]
    L += ["", "### 3. 포함 대상의 신청기간 유형 (신청기간 aplyYmd 에만 공통 규칙 적용, 사업기간 미사용)", "",
          "| 유형 | 건수 |", "|---|---:|"]
    L += _counter_lines(Counter(it.fields.get("period_kind") for it in inc_items))
    aux = sum(1 for it in inc_items if it.flags.get("period_aux"))
    L += ["", f"신청기간(aplyYmd)이 비어 지원내용·신청방법의 기한 문장으로 '사유 발생 후 기한' 분류: {aux}건 (날짜 계산 없음)"]
    st = Counter(R.apply_status(it.to_record(), src.today) for it in inc_items) if getattr(src, "today", None) else Counter()
    if st:
        L += ["", f"기준일 {src.today} 신청 상태: " + " · ".join(f"{k} {st.get(k, 0)}" for k in R.STATUS_ORDER if st.get(k))]
    links = Counter()
    for it in inc_items:
        fl = it.flags
        links["링크 1개 이상"] += bool(fl.get("links"))
        links["신청 링크"] += any(x["field"] == "aplyUrlAddr" for x in fl.get("links", []))
        links["참고 링크"] += any(x["field"].startswith("refUrl") for x in fl.get("links", []))
        links["주소 형식 아닌 링크 값"] += bool(fl.get("link_texts"))
        links["링크 없음(공식 안내 링크 확인 필요)"] += bool(fl.get("link_missing"))
    L += ["", "### 4. 포함 대상의 링크 (응답 값만, 온통청년 상세 주소는 만들지 않음)", "", "| 구분 | 건수 |", "|---|---:|"]
    L += [f"| {k} | {links.get(k, 0)} |" for k in ("링크 1개 이상", "신청 링크", "참고 링크", "주소 형식 아닌 링크 값",
                                                   "링크 없음(공식 안내 링크 확인 필요)")]

    L += ["", "### 5. 실제 응답의 온통청년 분류값 분포 (판정별)", ""]
    for fld, label, split in (("lclsfNm", "대분류", False), ("mclsfNm", "중분류", False), ("plcyKywdNm", "키워드", True)):
        allc = Counter()
        per = {v: Counter() for v in (INCLUDE, REVIEW, EXCLUDE)}
        for row, d, _ in decs:
            vals = _split_kw(row.get(fld)) if split else [T.clean(row.get(fld)) or "(빈 값)"]
            for v in vals:
                allc[v] += 1
                per[d.verdict][v] += 1
        L += [f"**{label} ({fld})** — 값 {len(allc)}종", "", "| 값 | 전체 | 포함 | 분류 검토 | 제외 |", "|---|---:|---:|---:|---:|"]
        L += [f"| {k} | {n} | {per[INCLUDE][k]} | {per[REVIEW][k]} | {per[EXCLUDE][k]} |" for k, n in allc.most_common(40)]
        if len(allc) > 40:
            L.append(f"| (나머지 {len(allc) - 40}종) | | | | |")
        L.append("")

    L += ["### 6. 응답 필드 목록 (비어 있지 않은 행 수, 값 종류가 적으면 상위 값)", "", "| 필드 | 값 있는 행 | 상위 값 |", "|---|---:|---|"]
    keys = Counter(k for r in rows for k in r)
    for k, _ in sorted(keys.items()):
        vals = Counter(str(r.get(k)).strip() for r in rows if T.clean(r.get(k)))
        top = " / ".join(f"{v[:20]}({c})" for v, c in vals.most_common(5)) if len(vals) <= 30 else f"값 {len(vals)}종"
        L.append(f"| {k} | {sum(vals.values())} | {top} |")

    L += ["", "### 7. 분야별 포함 예시", ""]
    for cat in ("생활비", "주거비", "장학금"):
        ex = [(r, d, it) for r, d, it in decs if d.verdict == INCLUDE and d.category == cat][:EXAMPLES_PER]
        L.append(f"**{cat}**")
        for r, d, it in ex:
            rec = it.to_record()
            lk = ", ".join(f"{x['type']} {x['url']}" for x in it.flags.get("links", [])) or "링크 없음"
            L += [f"- {rec['title']} ({rec.get('organizer') or '기관 미기재'}) · 정책번호 {it.source_id}",
                  f"  - 판정 근거: {d.basis_text()[:300]}",
                  f"  - 혜택: {(rec.get('benefit_text') or '미기재')[:150]}",
                  f"  - 신청기간 원문: {rec.get('period_text') or '비어 있음'} → 유형 {rec.get('period_kind')}",
                  f"  - 링크: {lk}"]
        L.append("")
    L += ["### 8. 분류 검토 예시 (사유별 2건)", ""]
    for reason, _ in Counter(d.reason for _, d, _ in decs if d.verdict == REVIEW).most_common():
        L.append(f"**{reason}**")
        for r, d, _ in [x for x in decs if x[1].verdict == REVIEW and x[1].reason == reason][:2]:
            L.append(f"- {T.clean(r.get('plcyNm'))} · 근거: {d.basis_text()[:200]}")
        L.append("")
    L += ["### 9. 지난 시험에서 지적된 예시의 이번 판정", "", "| 찾은 정책명 | 판정 | 분야 | 사유·근거 |", "|---|---|---|---|"]
    for w in WATCHLIST:
        hits = [(r, d) for r, d, _ in decs if w.replace(" ", "") in (T.clean(r.get("plcyNm")) or "").replace(" ", "")]
        if not hits:
            L.append(f"| '{w}' 포함 정책 없음 | - | - | - |")
        for r, d in hits[:3]:
            L.append(f"| {T.clean(r.get('plcyNm'))} | {d.verdict} | {d.category or '-'} | "
                     f"{(d.reason + ' · ' if d.reason else '')}{d.basis_text()[:160].replace('|', '/')} |")
    return "\n".join(L) + "\n"


def write_review(src, out_dir: str | Path, *, include_raw: bool, secret: str | None) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    md = build_report(src)
    rows_csv = [["판정", "분야(잠정 포함)", "사유", "근거", "정책번호", "정책명", "주관기관", "대분류", "중분류", "키워드",
                 "신청기간 원문(aplyYmd)", "신청기간 유형", "사업기간 시작", "사업기간 끝", "신청 링크", "참고 링크 1", "참고 링크 2", "장학금 분야 지원유형", "보조 추출 기한 문장"]]
    for r, d, it in src.decisions:
        g = lambda k: T.clean(r.get(k)) or ""
        rows_csv.append([d.verdict, d.category or "", d.reason, d.basis_text(), g("plcyNo"), g("plcyNm"),
                         g("sprvsnInstCdNm"), g("lclsfNm"), g("mclsfNm"), g("plcyKywdNm"), g("aplyYmd"),
                         (it.fields.get("period_kind") if it else R.classify_period(g("aplyYmd")).kind), g("bizPrdBgngYmd"), g("bizPrdEndYmd"),
                         g("aplyUrlAddr"), g("refUrlAddr1"), g("refUrlAddr2"), d.aid_kind or "",
                         ((it.flags.get("period_aux") or {}).get("sentence") if it else "") or ""])
    snap = None
    if include_raw:
        snap = json.dumps({"note": "온통청년 Open API 응답 원본(result.youthPolicyList 전체). 인증키·요청 주소 없음",
                           "fetched_rows": len(src.rows), "api_total": src.total, "pages": src.pages,
                           "page_log": src.page_log, "rows": src.rows}, ensure_ascii=False)
    # 인증키가 어디에도 들어가지 않았는지 확인
    if secret:
        for blob in (md, json.dumps(rows_csv, ensure_ascii=False), snap or ""):
            if secret in blob:
                raise RuntimeError("시험 보고서에 인증키 문자열이 들어 있어 쓰지 않음")
    (out / "youthcenter_report.md").write_text(md, encoding="utf-8")
    with (out / "youthcenter_decisions.csv").open("w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerows(rows_csv)
    # 분류 검토 대상만 따로(확인 완료가 아님 — 사람이 판정할 목록)
    with (out / "youthcenter_review_needed.csv").open("w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerows([["처리 상태"] + rows_csv[0]] +
                                [[REVIEW_STATUS] + r for r in rows_csv[1:] if r[0] == REVIEW])
    if snap is not None:
        with gzip.open(out / "youthcenter_raw.json.gz", "wt", encoding="utf-8") as f:
            f.write(snap)
    return out / "youthcenter_report.md"


def rereview(raw_path: str | Path, out_dir: str | Path, today: str | None = None) -> Path:
    """보관한 스냅숏으로 판정만 다시 실행(API 호출 없음)."""
    from .sources.youthcenter import YouthCenter
    with gzip.open(raw_path, "rt", encoding="utf-8") as f:
        snap = json.load(f)
    yc = YouthCenter(api_key="")
    yc.rows, yc.total, yc.pages, yc.page_log = snap["rows"], snap.get("api_total"), snap.get("pages"), snap.get("page_log", [])
    yc.today = today
    yc.classify_rows(yc.rows)
    return write_review(yc, out_dir, include_raw=False, secret=None)


def compare(before_csv: str | Path, after_csv: str | Path, out_md: str | Path) -> dict:
    """수정 전·후 판정표(youthcenter_decisions.csv)를 정책번호로 비교한다."""
    def load(p):
        with open(p, encoding="utf-8-sig", newline="") as f:
            return {r["정책번호"]: r for r in csv.DictReader(f)}
    a, b = load(before_csv), load(after_csv)
    key = lambda r: (r["판정"], r["분야(잠정 포함)"])
    cnt = lambda d: Counter(key(r) for r in d.values())
    ca, cb = cnt(a), cnt(b)
    moved = Counter((key(a[i]), key(b[i])) for i in a.keys() & b.keys() if key(a[i]) != key(b[i]))
    L = ["## 수정 전·후 판정 비교 (같은 응답 스냅숏, API 재호출 없음)", "",
         f"- 비교 행: 수정 전 {len(a)} · 수정 후 {len(b)} · 공통 정책번호 {len(a.keys() & b.keys())}", "",
         "| 판정 | 분야 | 수정 전 | 수정 후 |", "|---|---|---:|---:|"]
    for k in sorted(set(ca) | set(cb)):
        L.append(f"| {k[0]} | {k[1] or '-'} | {ca.get(k, 0)} | {cb.get(k, 0)} |")
    L += ["", "**판정이 바뀐 묶음**", "", "| 수정 전 | 수정 후 | 건수 | 예시 |", "|---|---|---:|---|"]
    for (x, y), n in moved.most_common():
        ex = [b[i]["정책명"] for i in a.keys() & b.keys() if key(a[i]) == x and key(b[i]) == y][:3]
        L.append(f"| {x[0]}·{x[1] or '-'} | {y[0]}·{y[1] or '-'} | {n} | {' / '.join(ex)} |")
    Path(out_md).write_text("\n".join(L) + "\n", encoding="utf-8")
    return {"before": dict(ca), "after": dict(cb), "moved": sum(moved.values())}
