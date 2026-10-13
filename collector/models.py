"""표준 공고 레코드. schema/notice-data.schema.json 의 notice 정의와 같은 필드를 쓴다."""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))

# 수집기가 채우는 내용 필드(변경 감지 대상). 시각·이력·검토 상태는 제외.
CONTENT_FIELDS = [
    "category", "title", "organizer", "info_type",
    "target_text", "youth_relevant", "youth_basis", "age_condition", "region", "region_condition",
    "income_condition", "status_condition", "benefit_text", "support_type",
    "amount_text", "amount_value", "amount_unit", "amount_period", "amount_qualifier",
    "apply_start", "apply_end", "apply_end_time", "period_text", "apply_status_override",
    "apply_method", "apply_url", "required_docs", "exclusions", "official_url", "attachment_url",
    "posted_at", "extra",
    "period_kind", "period_flags", "period_month_end", "period_closed_year", "review_notes", "amount_items",
]

# 출처 원문을 그대로 옮긴 필드. 이 값이 바뀌면 '공고 내용 변경', 나머지(분류 결과)만 바뀌면 '분류 규칙 변경'.
# (원본 요약값 flags.source_digest 가 있으면 그것으로 판단하고, 없을 때만 이 목록으로 판단)
SOURCE_FIELDS = {"title", "organizer", "target_text", "benefit_text", "period_text", "apply_method",
                 "apply_url", "required_docs", "posted_at", "official_url", "attachment_url"}

EXTRA_KEYS = {
    "공모전": ["contest_field", "theme", "team_type", "submission", "prize_text"],
    "장학금": ["sch_kind", "pay_amount", "pay_freq", "grade_cond", "year_cond", "income_cond", "dup_allowed"],
    "생활비": ["amount_period_text", "budget_end", "household", "housing", "self_pay", "loan_terms"],
    "주거비": ["amount_period_text", "budget_end", "household", "housing", "self_pay", "loan_terms"],
}

BASE: dict = {
    "id": None, "category": None, "title": None, "organizer": None, "info_type": None, "sources": [],
    "target_text": None, "youth_relevant": None, "youth_basis": None, "age_condition": None,
    "region": None, "region_condition": None, "income_condition": None, "status_condition": None,
    "benefit_text": None, "support_type": None, "amount_text": None, "amount_value": None,
    "amount_unit": None, "amount_period": None, "amount_qualifier": None,
    "apply_start": None, "apply_end": None, "apply_end_time": None, "period_text": None,
    "apply_status_override": None, "apply_method": None, "apply_url": None, "required_docs": None,
    "exclusions": None, "official_url": None, "attachment_url": None, "posted_at": None,
    "first_seen_at": None, "last_checked_at": None, "last_changed_at": None,
    "changed_fields": [], "needs_review_fields": [], "review_status": "미검토", "review_note": None,
    "extra": {}, "evidence": {}, "history": [], "related": [], "conflicts": [], "flags": {},
    "period_kind": None, "period_flags": [], "period_month_end": None, "period_closed_year": None,
    "review_notes": [], "amount_items": [],
}


def now_kst() -> datetime:
    return datetime.now(KST).replace(microsecond=0)


def iso(dt: datetime) -> str:
    return dt.astimezone(KST).replace(microsecond=0).isoformat()


def internal_id(source_key: str, source_id: str) -> str:
    h = hashlib.sha1(f"{source_key}:{source_id}".encode()).hexdigest()[:10]
    return f"N-{h}"


@dataclass
class Item:
    """출처 모듈이 돌려주는 정규화된 공고 1건(저장 전)."""
    source_key: str
    source_name: str
    source_id: str              # 출처 공고 ID. 없으면 정규화 URL
    source_url: str | None
    site_category: str | None
    fields: dict                # BASE의 내용 필드 일부
    evidence: dict = field(default_factory=dict)
    needs_review: list = field(default_factory=list)
    source_digest: str | None = None   # 출처 원본 응답(조회수 등 제외)의 해시
    conflicts: list = field(default_factory=list)   # 같은 출처 안에서 원문끼리 충돌(예: 원문 연령 ↔ 지원조건 코드)

    def to_record(self) -> dict:
        rec = copy.deepcopy(BASE)
        for k, v in self.fields.items():
            if k not in rec:
                raise KeyError(f"정의되지 않은 필드: {k}")
            rec[k] = v
        # 분야별 추가 항목: 정의된 키를 모두 갖추고 없는 값은 None
        cat = rec.get("category")
        extra = {k: None for k in EXTRA_KEYS.get(cat, [])}
        extra.update({k: v for k, v in (rec.get("extra") or {}).items()})
        rec["extra"] = extra
        rec["id"] = internal_id(self.source_key, self.source_id)
        rec["sources"] = [{
            "site": self.source_name, "source_key": self.source_key, "source_id": self.source_id,
            "url": self.source_url, "site_category": self.site_category, "note": None,
        }]
        rec["evidence"] = {k: v for k, v in self.evidence.items() if v}
        rec["needs_review_fields"] = sorted(set(self.needs_review))
        rec["conflicts"] = [c for c in self.conflicts if c]
        if self.source_digest:
            rec["flags"] = {**rec.get("flags", {}), "source_digest": self.source_digest}
        return rec


def content_of(rec: dict) -> dict:
    return {k: rec.get(k) for k in CONTENT_FIELDS} | {"needs_review_fields": rec.get("needs_review_fields", []),
                                                    "conflicts": rec.get("conflicts", [])}


def content_hash(rec: dict) -> str:
    return hashlib.sha256(json.dumps(content_of(rec), ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def diff(old: dict, new: dict) -> list[dict]:
    """내용 필드 중 바뀐 항목. extra는 하위 키 단위로 비교."""
    out = []
    for k in CONTENT_FIELDS:
        a, b = old.get(k), new.get(k)
        if k == "extra":
            a, b = a or {}, b or {}
            for kk in sorted(set(a) | set(b)):
                if a.get(kk) != b.get(kk):
                    out.append({"field": kk, "before": a.get(kk), "after": b.get(kk)})
        elif a != b:
            out.append({"field": k, "before": a, "after": b})
    return out
