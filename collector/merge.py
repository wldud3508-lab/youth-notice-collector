"""출처 간 '같은 정책 후보' 연결.

- 출처가 다르면 절대 하나로 합치지 않는다. 각 출처의 기록(원문·신청기간·링크·최초 수집/마지막 확인 시각·검토 상태)을
  그대로 두고, 같은 정책으로 의심되는 기록끼리 related 에 '같은 정책 후보(다른 출처)'로 연결만 한다.
- 다른 출처의 값으로 채우거나 덮어쓰지 않는다. 연결 때문에 검토 상태·확인 필요 항목을 바꾸지 않는다.
- 의심 기준: 공고명(공백·기호 정규화)이 같음. 기관명·신청기간·신청 링크가 같은지는 메모로만 남긴다.
"""
from __future__ import annotations

import copy

from .textutil import norm_key

CROSS = "같은 정책 후보(다른 출처)"


def _src(rec):
    return (rec.get("sources") or [{}])[0].get("source_key") or ""


def _site(rec):
    return (rec.get("sources") or [{}])[0].get("site") or _src(rec)


def _compare(a, b) -> str:
    same = lambda x, y: x is not None and x == y
    parts = [
        "기관명 " + ("같음" if norm_key(a.get("organizer")) and same(norm_key(a.get("organizer")), norm_key(b.get("organizer"))) else "다름·미기재"),
        "신청기간 " + ("같음" if a.get("apply_end") and same((a.get("apply_start"), a.get("apply_end")),
                                                          (b.get("apply_start"), b.get("apply_end"))) else "다름·미확정"),
        "신청 링크 " + ("같음" if a.get("apply_url") and same((a.get("apply_url") or "").rstrip("/"),
                                                          (b.get("apply_url") or "").rstrip("/")) else "다름·미기재"),
    ]
    return f"공고명이 같아 연결({_site(a)} ↔ {_site(b)}). " + ", ".join(parts) + ". 자동으로 합치지 않음 — 각 출처 원문 확인"


def link_records(records: list[dict]) -> list[dict]:
    recs = [copy.deepcopy(r) for r in records]
    groups: dict[str, list[dict]] = {}
    for r in recs:
        k = norm_key(r.get("title"))
        if k:
            groups.setdefault(k, []).append(r)
    for group in groups.values():
        if len({_src(r) for r in group}) < 2:
            continue
        for a in group:
            for b in group:
                if a is b or _src(a) == _src(b):
                    continue
                if any(x.get("id") == b["id"] for x in a["related"]):
                    continue
                a["related"].append({"type": CROSS, "id": b["id"], "note": _compare(a, b)})
    return recs


merge_records = link_records   # 이전 이름 호환. 더 이상 병합하지 않음


def cross_source_pairs(records: list[dict]) -> dict[str, int]:
    """출처별로 '다른 출처와 같은 정책 후보'가 있는 기록 수."""
    out: dict[str, int] = {}
    for r in records:
        if any(x.get("type") == CROSS for x in r.get("related") or []):
            out[_src(r)] = out.get(_src(r), 0) + 1
    return out
