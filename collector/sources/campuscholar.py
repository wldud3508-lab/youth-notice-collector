"""캠퍼스칼라(CampuScholar) — 공식 RSS 피드 https://campuscholar.com/feed/

사이트 식별은 사용자 확인 전이다. CAMPUSCHOLAR_CONFIRMED=1 일 때만 수집한다.
이 사이트는 장학금 안내 글(2차 정보)을 올리고, 정보 이용 안내에서 무단 전문 복제와 상업적 재배포를 금지한다.
그래서 본문은 저장하지 않고 제목·링크·게시일·사이트 분류만 저장한다. 신청기간은 주최기관 공고로 확인해야 하므로
날짜는 비워 두고 '확인 필요'로 표시한다.
"""
from __future__ import annotations

import os
import re
import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from .. import textutil as T
from ..models import KST, Item
from .base import FetchResult, Source, SourceInfo, SourceUnavailable, UnexpectedResponse, http_get

FEED = "https://campuscholar.com/feed/"
MAX_PAGES = 5


class CampuScholar(Source):
    info = SourceInfo(
        key="campuscholar", name="캠퍼스칼라", categories=["장학금"], method="공식 RSS",
        connection="공식 RSS (제목·링크·게시일·분류만 저장, 본문 저장 안 함)",
        stage="허가 확인 중",
        stage_note="RSS 응답 확인. 사이트 식별은 사용자 확인 대기, 정보 이용 안내상 전문 복제·상업적 재배포 금지·출처 표시 시 일부 인용 허용 — RSS 자동 이용 조항은 없어 문의 필요",
    )

    def __init__(self, confirmed: bool | None = None, http=http_get):
        self.confirmed = confirmed if confirmed is not None else os.getenv("CAMPUSCHOLAR_CONFIRMED") == "1"
        self.http = http

    def _parse(self, xml_text: str) -> list[dict]:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            raise UnexpectedResponse(f"RSS XML 해석 실패: {e}") from e
        ch = root.find("channel")
        if ch is None:
            raise UnexpectedResponse("RSS channel 요소 없음")
        out = []
        for it in ch.findall("item"):
            out.append({
                "title": T.clean(it.findtext("title")),
                "link": T.clean(it.findtext("link")),
                "guid": T.clean(it.findtext("guid")),
                "pubDate": T.clean(it.findtext("pubDate")),
                "categories": [T.clean(c.text) for c in it.findall("category") if T.clean(c.text)],
            })
        return out

    def fetch(self, *, since: str | None = None) -> FetchResult:
        if not self.confirmed:
            raise SourceUnavailable("연결 보류", "캠퍼스칼라 사이트 식별(campuscholar.com) 사용자 확인 대기")
        rows: list[dict] = []
        for page in range(1, MAX_PAGES + 1):
            params = {"paged": page} if page > 1 else None
            try:
                r = self.http(FEED, params=params, accept="application/rss+xml, application/xml;q=0.9")
            except Exception as e:  # noqa: BLE001
                if page > 1 and getattr(getattr(e, "response", None), "status_code", None) == 404:
                    break
                raise
            items = self._parse(r.text)
            rows.extend(items)
            oldest = min((self._pub(i) or "9999") for i in items) if items else None
            # 마지막 성공 이후 글을 모두 받았으면 멈춤(누락 확인용으로 2일 여유)
            if not items or not since or (oldest and oldest[:10] < _minus_days(since, 2)):
                break
            time.sleep(1)
        items, skipped = [], {}
        seen = set()
        for row in rows:
            item, why = self.map_row(row)
            if item and item.source_id not in seen:
                seen.add(item.source_id)
                items.append(item)
            elif not item:
                skipped[why] = skipped.get(why, 0) + 1
        return FetchResult(items=items, fetched_raw=len(rows), skipped=skipped)

    @staticmethod
    def _pub(row) -> str | None:
        try:
            return parsedate_to_datetime(row["pubDate"]).astimezone(KST).isoformat()
        except (TypeError, ValueError, KeyError):
            return None

    def map_row(self, row: dict) -> tuple[Item | None, str]:
        title, link = row.get("title"), row.get("link")
        if not title or not link:
            return None, "제목·링크 없음"
        cats = row.get("categories") or []
        joined = ", ".join(cats)
        if not any("장학" in c for c in cats):
            return None, "장학금 분류가 없는 글(안내·정보 글 등)"
        if not re.search(r"대학|대학원|청년|신입생", joined + title):
            return None, "대학생·청년 대상 분류 없음(고등학생 전용 등)"
        need = ["info_type", "apply_start", "apply_end", "organizer"]
        ev = {"title": "RSS 제목", "category": "캠퍼스칼라 RSS 분류: " + joined[:200]}
        org = None
        if ":" in title:
            cand = title.rsplit(":", 1)[1].strip()
            cand = re.sub(r"\s*장학금$", "", cand).strip()
            if cand:
                org = cand
                ev["organizer"] = f"제목에서 추출('{title.rsplit(':', 1)[1].strip()}') — 주최기관 공고로 확인 필요"
        months = [c.replace(" 장학금", "") for c in cats if re.fullmatch(r"\d{1,2}월 장학금", c)]
        if months:
            ev["period_text"] = "사이트 분류상 모집 시기: " + ", ".join(months) + " (실제 신청기간 아님)"
        kinds = [k for k, pat in (("생활비", "생활비"), ("등록금", "등록금")) if any(pat in c for c in cats)]
        years = [c for c in cats if "학년" in c or "신입생" in c]
        amt = T.parse_amount(title)
        if amt.evidence:
            ev["amount_text"] = "RSS 제목: " + title
        pub = self._pub(row)
        fields = {
            "category": "장학금", "title": title, "organizer": org, "info_type": None,
            "target_text": None, "youth_relevant": True,
            "youth_basis": "사이트 분류에 대학(원)생·청년 대상 표시: " + ", ".join(c for c in cats if re.search(r"대학|청년|신입생", c))[:150],
            "benefit_text": None, "support_type": "무상지원" if "장학금" in title else None,
            "amount_text": amt.text, "amount_value": amt.value, "amount_unit": amt.unit,
            "amount_period": amt.period, "amount_qualifier": amt.qualifier,
            "period_text": None, "posted_at": pub[:10] if pub else None,
            "income_condition": "저소득층 대상 분류" if any("저소득" in c or "취약계층" in c for c in cats) else None,
            "extra": {
                "sch_kind": " · ".join(kinds) if kinds else None,
                "year_cond": ", ".join(years) if years else None,
                "income_cond": "사이트 분류: 저소득층·취약계층" if any("저소득" in c or "취약계층" in c for c in cats) else None,
            },
        }
        sid = row.get("guid") or link
        return Item("campuscholar", "캠퍼스칼라", sid, link, joined[:200] or None, fields, ev, need), ""


def _minus_days(iso_dt: str, n: int) -> str:
    from datetime import date, timedelta
    d = date.fromisoformat(iso_dt[:10]) - timedelta(days=n)
    return d.isoformat()
