"""다른 출처의 같은 공고 연결.

- 기관명·제목·신청 시작일·마감일이 모두 같고, 공식 신청 링크가 같거나 한쪽이 없으면 → 하나로 합치고 출처 링크를 모두 보존.
- 제목만 같고 날짜·링크가 다르면 → 자동 병합하지 않고 '중복 후보'로 연결, 확인 필요 표시.
- 신청 연도가 다르면 → '다른 회차'로만 연결(별개 공고).
"""
from __future__ import annotations

import copy

from .textutil import norm_key

PRIORITY = ["gov24", "youthcenter", "kosaf", "campuscholar", "manual"]
CONFLICT_FIELDS = ["amount_text", "apply_start", "apply_end", "age_condition", "support_type"]


def _src(rec):
    return (rec.get("sources") or [{}])[0].get("source_key") or ""


def _rank(rec):
    k = _src(rec)
    return PRIORITY.index(k) if k in PRIORITY else len(PRIORITY)


def _same(a, b) -> bool:
    if norm_key(a.get("organizer")) != norm_key(b.get("organizer")) or not norm_key(a.get("organizer")):
        return False
    if not (a.get("apply_start") and a.get("apply_end")):
        return False
    if (a.get("apply_start"), a.get("apply_end")) != (b.get("apply_start"), b.get("apply_end")):
        return False
    ua, ub = a.get("apply_url"), b.get("apply_url")
    return not ua or not ub or ua.rstrip("/") == ub.rstrip("/")


def merge_records(records: list[dict]) -> list[dict]:
    recs = [copy.deepcopy(r) for r in records]
    groups: dict[str, list[dict]] = {}
    for r in recs:
        groups.setdefault(norm_key(r.get("title")), []).append(r)
    out, absorbed = [], set()
    for key, group in groups.items():
        if len(group) < 2 or not key:
            continue
        group.sort(key=_rank)
        for i, a in enumerate(group):
            if a["id"] in absorbed:
                continue
            for b in group[i + 1:]:
                if b["id"] in absorbed or _src(a) == _src(b):
                    continue
                if _same(a, b):
                    absorbed.add(b["id"])
                    a["sources"] = a["sources"] + b["sources"]
                    for f in CONFLICT_FIELDS:
                        va, vb = a.get(f), b.get(f)
                        if va is not None and vb is not None and va != vb:
                            a["conflicts"].append({"field": f, "values": [
                                {"site": a["sources"][0]["site"], "value": va}, {"site": b["sources"][0]["site"], "value": vb}]})
                            a["needs_review_fields"] = sorted(set(a["needs_review_fields"]) | {f})
                        elif va is None and vb is not None:
                            a[f] = vb
                    a["flags"]["merged"] = "기관명·제목·신청기간이 일치하고 공식 신청 링크가 같거나 한쪽에 없어 연결"
                    a["history"] = sorted(a["history"] + b["history"], key=lambda h: h["at"], reverse=True)
                    a["first_seen_at"] = min(a["first_seen_at"], b["first_seen_at"])
                else:
                    ya, yb = (a.get("apply_start") or "")[:4], (b.get("apply_start") or "")[:4]
                    if ya and yb and ya != yb:
                        a["related"].append({"type": "다른 회차", "id": b["id"], "note": "신청 연도가 달라 별개 공고로 유지"})
                        b["related"].append({"type": "다른 회차", "id": a["id"], "note": "신청 연도가 달라 별개 공고로 유지"})
                    else:
                        note = "제목이 같지만 기관·신청기간·신청 링크 중 일치하지 않는 항목이 있어 자동 병합하지 않음"
                        a["related"].append({"type": "중복 후보", "id": b["id"], "note": note})
                        b["related"].append({"type": "중복 후보", "id": a["id"], "note": note})
                        for r in (a, b):
                            r["needs_review_fields"] = sorted(set(r["needs_review_fields"]) | {"중복 여부"})
    for r in recs:
        if r["id"] not in absorbed:
            r["related"] = [x for x in r["related"] if x.get("id") not in absorbed or x.get("id") is None]
            out.append(r)
    return out
