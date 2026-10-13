"""수동 입력 공고 (자동수집이 보류된 출처용).

manual/notices.json 에 다음 형식으로 적는다. 적지 않은 항목은 null로 저장되고 화면에 '미기재'로 보인다.
[
  {
    "source_site": "링커리어",                       # 공고를 발견한 곳
    "source_url": "https://...",                     # 게시물 주소 (필수)
    "category": "공모전",                            # 공모전/장학금/생활비/주거비 (필수)
    "title": "공고명",                                # 필수
    "organizer": "주최기관", "period_text": "2026. 10. 1. ~ 10. 31.",
    "benefit_text": "...", "official_url": "https://...", "apply_url": "https://...",
    "target_text": "...", "region": "전국", "note": "메모"
  }
]
신청 시작·마감일은 period_text 원문에서 규칙으로만 뽑는다.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from . import textutil as T
from .models import Item

ALLOWED = {"organizer", "target_text", "region", "region_condition", "income_condition", "status_condition",
           "benefit_text", "apply_method", "apply_url", "required_docs", "exclusions", "official_url",
           "attachment_url", "age_condition"}


def normalize_url(url: str) -> str:
    """중복 판정용 URL: 스킴·호스트 소문자, 추적용 파라미터·조각 제거, 끝 슬래시 제거."""
    p = urlsplit(url.strip())
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not k.lower().startswith(("utm_", "fbclid", "gclid"))]
    path = p.path.rstrip("/") or "/"
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), path, urlencode(sorted(q)), ""))


def load_manual(path: str | Path) -> list[Item]:
    p = Path(path)
    if not p.exists():
        return []
    rows = json.loads(p.read_text(encoding="utf-8"))
    items = []
    for i, r in enumerate(rows):
        for req in ("source_url", "category", "title"):
            if not T.clean(r.get(req)):
                raise ValueError(f"manual/notices.json {i + 1}번째 항목에 {req} 가 없습니다")
        if r["category"] not in ("공모전", "장학금", "생활비", "주거비"):
            raise ValueError(f"{i + 1}번째 항목 category 값이 잘못됨: {r['category']}")
        fields = {k: T.clean(r.get(k)) for k in ALLOWED if T.clean(r.get(k))}
        period = T.clean(r.get("period_text"))
        p_ = T.parse_period(period)
        need = ["youth_relevant"] if r.get("youth_relevant") is None else []
        if p_.needs_review and not p_.always:
            need += ["apply_start", "apply_end"]
        amt = T.parse_amount(fields.get("benefit_text"))
        stype, _ = T.support_type(r["title"], fields.get("benefit_text"))
        fields.update({
            "category": r["category"], "title": T.clean(r["title"]),
            "info_type": "상시 제도 안내" if p_.always else "모집공고",
            "period_text": period, "apply_start": p_.start, "apply_end": p_.end, "apply_end_time": p_.end_time,
            "youth_relevant": r.get("youth_relevant"), "youth_basis": T.clean(r.get("youth_basis")),
            "amount_text": amt.text, "amount_value": amt.value, "amount_unit": amt.unit,
            "amount_period": amt.period, "amount_qualifier": amt.qualifier, "support_type": stype,
        })
        site = T.clean(r.get("source_site")) or "수동 입력"
        url = r["source_url"].strip()
        it = Item("manual", "수동 입력", normalize_url(url), url, f"발견 위치: {site}", fields,
                  {"title": "수동 입력: " + (T.clean(r.get("note")) or site)}, need)
        items.append(it)
    return items
