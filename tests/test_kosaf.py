"""한국장학재단 모듈 테스트. 열 이름은 공식 CSV(2026-09-10판)와 같고, 값은 모두 TEST 가상 값."""
import csv
from datetime import datetime

import pytest

from collector.export import export
from collector.models import KST
from collector.run import run_source
from collector.sources.base import SourceUnavailable
from collector.sources.kosaf import Kosaf, stable_id
from collector.store import Store

COLS = ["번호", "운영기관명", "상품명", "운영기관구분", "상품구분", "학자금유형구분", "대학구분", "학년구분", "학과구분",
        "성적기준 상세내용", "소득기준 상세내용", "지원내역 상세내용", "특정자격 상세내용", "지역거주여부 상세내용",
        "선발방법 상세내용", "선발인원 상세내용", "자격제한 상세내용", "추천필요여부 상세내용", "제출서류 상세내용",
        "홈페이지 주소", "모집시작일", "모집종료일"]


def row(no, org, name, kind="장학금", benefit="○ 1인당 100만원 (생활비 지원)", start="2026-10-01", end="2026-10-31", **kw):
    r = {c: "해당없음" for c in COLS}
    r.update({"번호": f"{no} ", "운영기관명": org, "상품명": name, "운영기관구분": "기타", "상품구분": kind,
              "학자금유형구분": "기타", "지원내역 상세내용": benefit, "모집시작일": start, "모집종료일": end,
              "홈페이지 주소": "https://example.com/TEST"})
    r.update(kw)
    return r


def write(path, rows):
    with open(path, "w", encoding="cp949", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows(rows)
    return str(path)


BASE_ROWS = [
    row(1, "(재)TEST장학회", "TEST 생활비 장학생"),
    row(2, "TEST시청", "대학생학자금대출이자지원", kind="학자금", benefit="○ 2025년도 발생이자 전액"),
    row(3, "TEST재단", "일반 상환 학자금대출", kind="학자금", benefit="○ 등록금 실소요액 대출"),
    row(4, "TEST군장학회", "TEST 지역인재", start="2025-03-02", end="2025-03-20"),
]


def day(n):
    return datetime(2026, 10, 16, 9, 0, tzinfo=KST).replace(day=16 + n)


def test_csv_mapping_and_loan_split(tmp_path):
    k = Kosaf(service_key="", path="", csv_path=write(tmp_path / "kosaf_univ_20260910.csv", BASE_ROWS))
    items = {it.fields["title"]: it for it in k.fetch().items}
    assert k.source_version == "2026-09-10" and k.mode == "공식 CSV"
    assert items["TEST 생활비 장학생"].fields["support_type"] == "무상지원"
    assert items["대학생학자금대출이자지원"].fields["support_type"] == "이자지원"
    assert items["일반 상환 학자금대출"].fields["support_type"] == "대출"
    rec = items["TEST 생활비 장학생"].to_record()
    assert rec["flags"]["source_cadence"] == "월간 파일" and rec["posted_at"] is None
    assert rec["apply_start"] == "2026-10-01" and rec["region"] is None          # '전국'으로 추정하지 않음


def test_stable_id_ignores_row_number_and_org_prefix():
    a = row(1, "(재)TEST장학회", "TEST 장학생")
    b = row(77, "TEST장학회", "TEST  장학생")
    c = row(1, "(재)TEST장학회", "TEST 장학생", start="2027-03-02")
    assert stable_id(a) == stable_id(b) and stable_id(a) != stable_id(c)          # 다음 회차는 별개


def test_new_file_version_does_not_duplicate(tmp_path):
    st = Store(tmp_path / "t.db")
    run_source(st, Kosaf(service_key="", path="", csv_path=write(tmp_path / "kosaf_univ_20260910.csv", BASE_ROWS)),
               "2026-10-16", force=False, log=lambda *_: None)
    v2 = [dict(r, **{"번호": f"{90 - i} "}) for i, r in enumerate(reversed(BASE_ROWS))]       # 번호·순서가 바뀐 새 파일
    v2[0] = dict(v2[0], **{"지원내역 상세내용": "○ 1인당 150만원 (TEST 변경)"})
    v2.append(row(5, "(재)TEST장학회", "TEST 생활비 장학생", start="2027-03-02", end="2027-03-20"))   # 다음 회차
    run_source(st, Kosaf(service_key="", path="", csv_path=write(tmp_path / "kosaf_univ_20261010.csv", v2)),
               "2026-10-17", force=False, log=lambda *_: None)
    r = st.latest_run("2026-10-17", "kosaf")
    assert (r["new"], r["changed"]) == (1, 1) and len(st.all_records()) == 5
    assert {x["flags"]["source_version"] for x in st.all_records()} == {"2026-10-10"}


def test_csv_run_is_not_real_connection_and_not_daily_new(tmp_path):
    st = Store(tmp_path / "t.db")
    k = Kosaf(service_key="", path="", csv_path=write(tmp_path / "kosaf_univ_20260910.csv", BASE_ROWS))
    today = datetime.now(KST).date().isoformat()
    run_source(st, k, today, force=False, log=lambda *_: None)
    d = export(st, [k], today, tmp_path / "out", include_manual=False)
    src = d["sources"][0]
    assert src["stage"] == "준비 완료" and src["fetch_mode"] == "공식 CSV" and src["source_version"] == "2026-09-10"
    s = (tmp_path / "out" / "summary.md").read_text(encoding="utf-8")
    found = s[s.index("## 오늘 발견"):s.index("## 일반 공고 예시")]
    assert "### 신규 0건" in found and "정기 자료 반영" in found and "처음 반영 4건" in found


def test_missing_settings_is_unconnected():
    with pytest.raises(SourceUnavailable) as e:
        Kosaf(service_key="", path="", csv_path="").fetch()
    assert e.value.status == "미연결" and "KOSAF_ODCLOUD_PATH" in str(e.value)
