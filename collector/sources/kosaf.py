"""한국장학재단 — 한국장학재단_학자금지원정보(대학생) (공공데이터포털 15028252, 파일데이터 + 자동변환 Open API).

확인한 것
- 열 구성(22개): 공개 저장소에 있는 공식 CSV 사본(2026-09-10판)으로 확인.
  번호, 운영기관명, 상품명, 운영기관구분, 상품구분(장학금/학자금), 학자금유형구분, 대학구분, 학년구분, 학과구분,
  성적기준·소득기준·지원내역·특정자격·지역거주여부·선발방법·선발인원·자격제한·추천필요여부·제출서류 상세내용,
  홈페이지 주소, 모집시작일, 모집종료일
- Open API 주소 형식: https://api.odcloud.kr/api/15028252/v1/uddi:<파일 버전 ID> (공개 프로젝트 코드로 교차 확인)
  인증은 serviceKey, 파라미터는 page/perPage/returnType. 파일이 갱신되면 uddi 가 새로 발급된다.
확인하지 못한 것: 공공데이터포털 명세 화면(자동 조회 차단). 경로는 활용신청 후 Swagger 화면에서 복사해 쓴다.

원칙
- 월 단위로 갱신되는 장학상품 목록이다. 매일 올라오는 모집공고처럼 '오늘 신규'로 보이지 않게 표시한다(source_cadence).
- 원본 파일 기준일(KOSAF_SOURCE_VERSION 또는 CSV 파일명의 날짜)과 우리 수집일을 따로 기록한다.
- 같은 장학금을 식별하는 열쇠: 정규화한 (운영기관명, 상품명, 모집시작일). '번호'는 파일마다 달라서 쓰지 않는다.
  같은 상품의 다음 회차(모집시작일이 다름)는 별개 공고로 저장한다.
- 상품구분 '학자금'(대출·이자지원)은 장학금과 지원방식으로 구분한다. 대출을 지원금으로 표시하지 않는다.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import unquote

from .. import rules as R
from .. import textutil as T
from ..models import Item
from .base import FetchResult, Source, SourceInfo, SourceUnavailable, UnexpectedResponse, http_get, redact

BASE = "https://api.odcloud.kr"
DATASET_URL = "https://www.data.go.kr/data/15028252/fileData.do"
CLIP = 600
CLIP_MARK = " …(이하 생략, 원문 링크 참고)"
CADENCE = "월간 파일"
REQUIRED = ("운영기관명", "상품명", "상품구분", "모집시작일", "모집종료일")
DETAIL = ["성적기준 상세내용", "소득기준 상세내용", "지원내역 상세내용", "특정자격 상세내용", "지역거주여부 상세내용",
          "선발방법 상세내용", "선발인원 상세내용", "자격제한 상세내용", "추천필요여부 상세내용", "제출서류 상세내용"]
DIGEST_VERSION = 1


def _v(row: dict, name: str) -> str | None:
    for k, v in row.items():
        if (k or "").replace(" ", "") == name.replace(" ", ""):
            v = T.clean(v)
            return None if v in (None, "-", "해당없음") else v
    return None


def _clip(s):
    return s[:CLIP].rstrip() + CLIP_MARK if s and len(s) > CLIP else s


def _key_part(s: str | None) -> str:
    s = re.sub(r"^\s*(\(재\)|\(사\)|재단법인|사단법인)\s*", "", s or "")
    return re.sub(r"\s+", "", s).lower()


def stable_id(row: dict) -> str:
    """파일 버전이 바뀌어도 같은 장학금이면 같은 값."""
    basis = "|".join([_key_part(_v(row, "운영기관명")), _key_part(_v(row, "상품명")), (_v(row, "모집시작일") or "")[:10]])
    return "kosaf-" + hashlib.sha1(basis.encode()).hexdigest()[:16]


def read_csv(path: str | Path) -> list[dict]:
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "cp949", "euc-kr"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise UnexpectedResponse("CSV 인코딩을 알 수 없음(utf-8/cp949 아님)")
    return [{(k or "").strip(): v for k, v in r.items()} for r in csv.DictReader(io.StringIO(text))]


class Kosaf(Source):
    info = SourceInfo(
        key="kosaf", name="한국장학재단", categories=["장학금"],
        method="공공데이터포털 15028252 (월간 파일, Open API 또는 공식 CSV)",
        connection="공공데이터포털 15028252 학자금지원정보(대학생), 월간 파일",
        stage="준비 완료", stage_note="코드 준비됨. 15028252 활용신청 승인과 API 경로(KOSAF_ODCLOUD_PATH) 필요",
    )

    def __init__(self, service_key=None, path=None, csv_path=None, source_version=None, http=http_get):
        raw = service_key if service_key is not None else os.getenv("DATA_GO_KR_SERVICE_KEY")
        self.key = unquote(raw.strip()) if raw else None
        self.path = (path if path is not None else os.getenv("KOSAF_ODCLOUD_PATH") or "").strip() or None
        self.csv_path = (csv_path if csv_path is not None else os.getenv("KOSAF_CSV_PATH") or "").strip() or None
        self.source_version = (source_version if source_version is not None else os.getenv("KOSAF_SOURCE_VERSION") or "").strip() or None
        self.http = http
        self.mode = None

    # ------------------------------------------------------------ 가져오기
    def _from_api(self) -> list[dict]:
        rows: list[dict] = []
        for page in range(1, 30):
            try:
                r = self.http(BASE + self.path, params={"serviceKey": self.key, "page": page, "perPage": 1000, "returnType": "JSON"})
            except Exception as e:  # noqa: BLE001
                status = getattr(getattr(e, "response", None), "status_code", None)
                if status in (401, 403):
                    raise SourceUnavailable("미연결", f"인증키 미승인 또는 15028252 활용신청 필요 (HTTP {status})") from e
                raise RuntimeError(redact(f"{page}쪽 요청 실패: {e}", self.key)) from e
            try:
                body = r.json()
            except ValueError as e:
                raise UnexpectedResponse("JSON이 아닌 응답") from e
            data = body.get("data") if isinstance(body, dict) else None
            if not isinstance(data, list):
                raise UnexpectedResponse("응답에 data 배열이 없음")
            rows.extend(data)
            total = body.get("totalCount") or body.get("matchCount")
            if not data or (isinstance(total, int) and len(rows) >= total):
                break
            if len(data) < 1000:
                raise UnexpectedResponse(f"{page}쪽 1000건 요청에 {len(data)}건만 옴(전체 {total}) — 페이지 상한 확인 필요")
            time.sleep(0.3)
        if isinstance(total, int) and len(rows) != total:
            raise UnexpectedResponse(f"전체 {total}건 중 {len(rows)}건만 받음")
        return rows

    def fetch(self, *, since: str | None = None) -> FetchResult:
        if self.csv_path:
            self.mode = "공식 CSV"
            if not Path(self.csv_path).exists():
                raise SourceUnavailable("미연결", f"KOSAF_CSV_PATH 파일이 없음: {Path(self.csv_path).name}")
            rows = read_csv(self.csv_path)
            if not self.source_version:
                m = re.search(r"(20\d{2})(\d{2})(\d{2})", Path(self.csv_path).name)
                self.source_version = f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None
        elif self.key and self.path:
            self.mode = "Open API"
            rows = self._from_api()
        else:
            missing = [n for n, v in (("DATA_GO_KR_SERVICE_KEY", self.key), ("KOSAF_ODCLOUD_PATH", self.path)) if not v]
            raise SourceUnavailable("미연결", "설정 없음: " + ", ".join(missing) + " (또는 시험용 KOSAF_CSV_PATH)")
        if not rows:
            raise UnexpectedResponse("받은 행이 0건")
        missing = [c for c in REQUIRED if _v(rows[0], c) is None and not any(c.replace(" ", "") == (k or "").replace(" ", "") for k in rows[0])]
        if missing:
            raise UnexpectedResponse("필수 열 없음: " + ", ".join(missing) + " / 실제 열: " + ", ".join(list(rows[0])[:25]))
        items, skipped, seen = [], {}, set()
        for row in rows:
            it, why = self.map_row(row)
            if not it:
                skipped[why] = skipped.get(why, 0) + 1
            elif it.source_id in seen:
                skipped["같은 파일 안 중복 행(기관·상품명·모집시작일 동일)"] = skipped.get("같은 파일 안 중복 행(기관·상품명·모집시작일 동일)", 0) + 1
            else:
                seen.add(it.source_id)
                items.append(it)
        return FetchResult(items=items, fetched_raw=len(rows), skipped=skipped)

    # ------------------------------------------------------------ 변환
    def map_row(self, row: dict) -> tuple[Item | None, str]:
        org, name = _v(row, "운영기관명"), _v(row, "상품명")
        if not org or not name:
            return None, "운영기관명·상품명 없음"
        kind = _v(row, "상품구분")                 # 장학금 / 학자금
        aid_type = _v(row, "학자금유형구분")       # 지역연고·성적우수 등(장학 유형, 지원방식 아님)
        benefit = _v(row, "지원내역 상세내용")
        start, end = T.ymd_compact(_v(row, "모집시작일")), T.ymd_compact(_v(row, "모집종료일"))
        need, notes, ev = [], [], {}
        if not start or not end:
            need += ["apply_start", "apply_end"]
        if start and end and start > end:
            start = end = None
            need += ["apply_start", "apply_end"]
            ev["apply_end"] = "원문 날짜 오류(시작일이 종료일보다 늦음)"
        period_text = f"모집시작일 {_v(row, '모집시작일') or '미기재'} ~ 모집종료일 {_v(row, '모집종료일') or '미기재'}"

        # 지원방식: 상품구분이 '학자금'이면 대출 또는 이자지원. 장학금으로 섞지 않는다.
        text = " ".join(x for x in (name, benefit) if x)
        if kind == "학자금":
            if re.search(r"이자\s*지원|이자를?\s*지원|발생\s*이자|이차\s*보전", text):
                stype = "이자지원"
            elif re.search(r"대출|융자|대부|대여", text):
                stype = "대출"
            else:
                stype = "기타"
                notes.append({"field": "support_type", "note": "상품구분 '학자금'이나 대출·이자지원 표현을 찾지 못함"})
        elif kind == "장학금":
            stype = "무상지원"
        else:
            stype = None
            need.append("support_type")
        if kind:
            ev["support_type"] = f"원본 상품구분: {kind}"

        am = R.amount_fields(benefit)
        if am["evidence"]:
            ev["amount_text"] = am["evidence"]
        notes += [{"field": "amount_text", "note": x} for x in am["notes"]]
        if am["need"]:
            need.append("amount_text")
        target_parts = [f"{k}: {_v(row, k)}" for k in ("대학구분", "학년구분", "학과구분") if _v(row, k)]
        special = _v(row, "특정자격 상세내용")
        af = R.age_fields(special, None)
        notes += [{"field": "age_condition", "note": x} for x in af["notes"]]
        region_txt = _v(row, "지역거주여부 상세내용")
        region = None
        if region_txt:
            hits = [nm for nm, pat in T.SIDO if re.search(pat, region_txt)]
            region = hits[0] if len(hits) == 1 else ("복수 지역" if hits else None)
        if region is None and aid_type == "지역연고":
            need.append("region")
        elif region is None:
            region = None   # 지역 조건 미기재 — '전국'으로 추정하지 않음

        clipped = [f for f, v in (("target_text", special), ("benefit_text", benefit),
                                  ("required_docs", _v(row, "제출서류 상세내용"))) if v and len(v) > CLIP]
        fields = {
            "category": "장학금", "title": name, "organizer": org,
            "info_type": "모집공고",
            "target_text": _clip(" / ".join(target_parts + ([special] if special else [])) or None),
            "youth_relevant": True, "youth_basis": "한국장학재단 학자금지원정보(대학생) 데이터셋",
            "age_condition": af["age_condition"], "region": region, "region_condition": _clip(region_txt),
            "income_condition": _clip(_v(row, "소득기준 상세내용")), "status_condition": _v(row, "학년구분"),
            "benefit_text": _clip(benefit), "support_type": stype,
            "amount_text": am["amount_text"], "amount_value": am["amount_value"], "amount_unit": am["amount_unit"],
            "amount_period": am["amount_period"], "amount_qualifier": am["amount_qualifier"], "amount_items": am["amount_items"],
            "apply_start": start, "apply_end": end, "period_text": period_text,
            "period_kind": R.KIND_DATED if (start or end) else R.KIND_NONE,
            "apply_method": _clip(_v(row, "선발방법 상세내용")), "required_docs": _clip(_v(row, "제출서류 상세내용")),
            "exclusions": _clip(_v(row, "자격제한 상세내용")), "official_url": _v(row, "홈페이지 주소"),
            "posted_at": None,   # 파일에 게시일 열이 없음(파일 기준일과 다름)
            "review_notes": notes,
            "extra": {
                "sch_kind": " / ".join(x for x in (kind, aid_type) if x) or None,
                "pay_amount": am["amount_text"], "pay_freq": None,
                "grade_cond": _clip(_v(row, "성적기준 상세내용")), "year_cond": _v(row, "학년구분"),
                "income_cond": _clip(_v(row, "소득기준 상세내용")), "dup_allowed": None,
            },
        }
        raw = {"list": {k: v for k, v in row.items() if k != "번호"}, "detail": {}, "cond": {}}
        digest = hashlib.sha256(json.dumps(raw, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:20]
        site_cat = " / ".join(x for x in (_v(row, "운영기관구분"), kind, aid_type) if x) or None
        flags = {"source_cadence": CADENCE, "source_version": self.source_version, "source_mode": self.mode}
        if clipped:
            flags["clipped_fields"] = clipped
        # 행마다 상세 페이지가 없으므로 출처 링크는 데이터셋 페이지(공식 홈페이지는 official_url)
        return Item("kosaf", "한국장학재단", stable_id(row), DATASET_URL, site_cat, fields, ev, need,
                    source_digest=digest, raw=raw, digest_version=DIGEST_VERSION,
                    flags={k: v for k, v in flags.items() if v}), ""
