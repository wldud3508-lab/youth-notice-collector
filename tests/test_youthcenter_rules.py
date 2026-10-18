"""온통청년 판정·기간·링크·원본 보관 시험. 정책명·내용은 2026-10-16 시험 실행 요약에 나온 실제 문구를 줄여 옮긴 것."""
import gzip
import json

import pytest

from collector.sources import youthcenter_rules as Y
from collector.sources.youthcenter import YouthCenter
from tests.fakes import FakeResp, youth_row


def dec(**kw):
    return Y.decide(youth_row(**kw))


@pytest.mark.parametrize("kw, verdict, cat, reason", [
    # 지난 시험에서 잘못 들어간 예시
    (dict(plcyNm="청년톡톡콘서트", plcyExplnCn="극작가가 경험을 강의하여 청년들에게 위로와 조언",
          plcySprtCn="일시 2026. 9. 17. 장소 은평문화예술회관 강사 극작가"), Y.EXCLUDE, None, Y.EX_EVENT),
    (dict(plcyNm="2025 홍천군 청년 일경험 지원사업", plcyExplnCn="기업의 다양한 직무경험을 통해 청년 채용 연계",
          plcySprtCn="기업이 청년을 채용(인턴)할 경우, 청년 인건비 및 교통비 월200만원을 6개월 지원"), Y.EXCLUDE, None, Y.EX_EMPLOYER),
    (dict(plcyNm="2026년 임신부 가사지원서비스 지원사업 시행", plcyExplnCn="임신부의 가사 어려움을 덜어주고 출산 준비",
          plcySprtCn="가사지원 서비스 이용 지원 지원금액 임신부 1인당 200,000원 한도"), Y.REVIEW, "생활비", Y.RV_MEDICAL_BIRTH),
    (dict(plcyNm="(광양시)난임부부 진단검사비 지원", plcyExplnCn="난임부부의 최초 진단검사비를 지원",
          plcySprtCn="난임 진단검사비 지원 부부당 최대 20만원 지원"), Y.REVIEW, "생활비", Y.RV_MEDICAL_BIRTH),
    (dict(plcyNm="빈곤 청년에 대한 근로인센티브 확대", plcyExplnCn="빈곤 청년에 대한 근로인센티브 확대",
          plcySprtCn="근로·사업소득 공제 40만원+30% 공제 등록금 공제 대학생의 등록금을 지출한 경우 해당 지출금액을 소득 산정 시 제외, "
                     "청년수당 등 다른 수당과 별도"), Y.REVIEW, "생활비", Y.RV_DEDUCTION),
    # 두 번째 시험에서 지적된 예시(실제 원문을 받지 못해 요지만 옮긴 시험 문장)
    (dict(plcyNm="2026 청년 관광두레 플러스사업", plcyExplnCn="지역 청년의 관광 분야 정착 지원",
          plcySprtCn="관광사업체 창업 및 사업화 자금 최대 3,000만원, 컨설팅"), Y.EXCLUDE, None, Y.EX_BUSINESS),
    (dict(plcyNm="청년안심주택 공급(매입)", plcyExplnCn="민간 주택을 매입하여 청년에게 공급",
          plcySprtCn="추진 경과: 청년·신혼부부 주거비 지원계획 수립, 주택 건립 기준 개정"), Y.REVIEW, "주거비", Y.RV_HOUSING_SUPPLY),
    (dict(plcyNm="청년안심주택 입주자 지원", plcyExplnCn="민간 주택을 매입하여 청년에게 공급",
          plcySprtCn="입주 청년에게 임대보증금 일부 지원"), Y.INCLUDE, "주거비", ""),   # 실제 개인 비용 지원이 있으면 포함
    (dict(plcyNm="대학생 천원의 아침밥 지원 확대", plcyExplnCn="아침식사 결식률이 높은 대학생에게 쌀 식단 제공",
          plcySprtCn="대학생들을 대상으로 쌀을 활용한 아침식사를 1천원에 제공"), Y.REVIEW, "생활비", Y.RV_INKIND),
    (dict(plcyNm="청년쉐어하우스 조성 및 운영", plcyExplnCn="주거취약계층 청년에게 저렴한 비용(월5만원)으로 주거를 지원",
          plcySprtCn="주거취약계층 청년에게 저렴한 비용(월5만원)으로 주거를 지원"), Y.REVIEW, "주거비", Y.RV_HOUSING_PROVIDE),
    # 범위 안
    (dict(plcyNm="인천시 청년월세 지원사업", plcySprtCn="1인 월 20만원씩 최대 24개월 월세지원"), Y.INCLUDE, "주거비", ""),
    (dict(plcyNm="2026년 군포시 신혼부부 및 청년 전월세 보증금 대출이자 지원", plcySprtCn="청년: 대출잔액의 1%, 최대 100만원"),
     Y.INCLUDE, "주거비", ""),
    (dict(plcyNm="대학생 학자금대출 이자 지원", plcySprtCn="한국장학재단을 통해 받은 대출금의 이자 지원"), Y.INCLUDE, "장학금", ""),
    (dict(plcyNm="충주시 청년 입영지원금", plcyExplnCn="입영하는 충주시 거주 청년들의 복지 증진",
          plcySprtCn="입영지원금 10만원 지원 지급방법 지역화폐카드 충전"), Y.INCLUDE, "생활비", ""),
    (dict(), Y.INCLUDE, "생활비", ""),     # 구직활동 지원금(기본 시험 행)
])
def test_decide_examples(kw, verdict, cat, reason):
    d = dec(**kw)
    assert (d.verdict, d.category, d.reason) == (verdict, cat, reason), d.basis
    assert d.basis and "온통청년 분류값" in d.basis[-1]       # 근거와 분류값을 항상 남김


def test_single_words_do_not_decide():
    # '지원금' 단어만 있고 금액·개인 지급 표현이 없으면 포함하지 않음
    d = dec(plcyNm="청년 지원금 안내", plcyExplnCn="안내", plcySprtCn="자세한 내용은 공고 참고")
    assert d.verdict == Y.REVIEW and d.reason == Y.RV_GENERIC_MONEY
    # 본문에만 '등록금'이 있으면 장학금으로 넣지 않음
    d = dec(plcyNm="청년 역량 프로그램", plcyExplnCn="역량 강화", plcySprtCn="등록금 납부 확인서 제출")
    assert d.verdict == Y.REVIEW and d.reason == Y.RV_BODY_TUITION
    # '인턴'이 있어도 기업에만 지급한다는 표현이 아니면 제외하지 않음
    d = dec(plcyNm="청년 인턴 활동비", plcyExplnCn="인턴 청년에게 활동비 지급", plcySprtCn="청년에게 월 30만원 지급")
    assert d.verdict == Y.REVIEW and d.reason == Y.RV_JOB


def test_youthcenter_period_uses_apply_field_only():
    yc = YouthCenter(api_key="K")
    rec = yc.map_row(youth_row(aplyYmd="", bizPrdBgngYmd="20250101", bizPrdEndYmd="20251231"))[0].to_record()
    assert rec["period_kind"] == "원문 기간 미기재" and rec["apply_start"] is None and rec["apply_end"] is None
    assert rec["period_text"] is None                          # 사업기간을 신청기간 원문 자리에 넣지 않음
    assert any("사업기간(신청기간 아님" in x["note"] and "2025-01-01" in x["note"] for x in rec["review_notes"])
    rec = yc.map_row(youth_row(aplyYmd="20260414 ~ 20260630"))[0].to_record()
    assert rec["period_kind"] == "기간 명시" and (rec["apply_start"], rec["apply_end"]) == ("2026-04-14", "2026-06-30")
    rec = yc.map_row(youth_row(aplyYmd="연중"))[0].to_record()
    assert rec["period_kind"] == "상시" and rec["apply_end"] is None


def test_youthcenter_links_typed_and_missing_flagged():
    yc = YouthCenter(api_key="K")
    rec = yc.map_row(youth_row(aplyUrlAddr="https://apply.example/TEST", refUrlAddr1="www.example.go.kr",
                               refUrlAddr2="https://ref.example/TEST"))[0].to_record()
    assert rec["apply_url"] == "https://apply.example/TEST" and rec["official_url"] is None
    assert [x["type"] for x in rec["flags"]["links"]] == ["신청 링크", "참고 링크 2"]
    assert rec["flags"]["link_texts"][0]["text"] == "www.example.go.kr"     # 주소를 고쳐 만들지 않음
    assert rec["sources"][0]["url"] is None                                 # 상세 주소 추측 안 함
    rec = yc.map_row(youth_row(aplyUrlAddr="", refUrlAddr1="", refUrlAddr2=""))[0].to_record()
    assert rec["flags"]["link_missing"] and "공식 안내 링크 확인 필요" in rec["needs_review_fields"]
    from collector.export import is_candidate
    rec["youth_relevant"] = True
    rec.update(apply_start="2026-10-01", apply_end="2026-12-31")
    assert not is_candidate(rec, "2026-10-16")


def test_youthcenter_raw_and_digest_kept():
    yc = YouthCenter(api_key="K")
    it = yc.map_row(youth_row(inqCnt=5))[0]
    assert it.raw["list"]["plcyNm"] and "inqCnt" not in it.raw["list"]
    it2 = yc.map_row(youth_row(inqCnt=999))[0]
    assert it.source_digest == it2.source_digest
    assert yc.map_row(youth_row(plcySprtCn="월 60만 원씩 최대 6개월 지급"))[0].source_digest != it.source_digest


def _http(rows):
    def http(url, params=None, **_):
        return FakeResp({"resultCode": 200, "resultMessage": "OK",
                         "result": {"pagging": {"totCount": len(rows), "pageNum": params["pageNum"], "pageSize": 100},
                                    "youthPolicyList": rows if params["pageNum"] == 1 else []}})
    return http


def test_duplicate_ids_counted_and_report_written(tmp_path):
    key = "TEST-SECRET-KEY-789"
    rows = [youth_row(), youth_row(),                                   # 같은 정책번호 2번
            youth_row(plcyNo="T2", plcyNm="청년톡톡콘서트", plcySprtCn="강연", plcyExplnCn="강연"),
            youth_row(plcyNo="T3", plcyNm="(광양시)난임부부 진단검사비 지원", plcyExplnCn="난임", plcySprtCn="최대 20만원 지원")]
    yc = YouthCenter(api_key=key, http=_http(rows))
    yc.today = "2026-10-16"
    r = yc.fetch()
    assert len(r.items) == 1 and yc.dup_ids == {"TEST-2026-001": 2}
    assert r.skipped["같은 정책번호 중복 행(첫 행만 저장)"] == 1
    assert r.skipped[f"제외: {Y.EX_EVENT}"] == 1 and r.skipped[f"분류 검토: {Y.RV_MEDICAL_BIRTH}"] == 1
    from collector.review_report import rereview, write_review
    write_review(yc, tmp_path / "review", include_raw=True, secret=key)
    md = (tmp_path / "review" / "youthcenter_report.md").read_text(encoding="utf-8")
    assert "API 전체 건수(totCount): 4 · 받은 행: 4" in md and "같은 정책번호 중복 행: 1행(1개 번호)" in md
    assert "| 청년톡톡콘서트 | 제외 |" in md
    for f in (tmp_path / "review").iterdir():                          # 어떤 파일에도 인증키 없음
        data = gzip.open(f, "rt", encoding="utf-8").read() if f.suffix == ".gz" else f.read_text(encoding="utf-8-sig")
        assert key not in data
    snap = json.loads(gzip.open(tmp_path / "review" / "youthcenter_raw.json.gz", "rt", encoding="utf-8").read())
    assert len(snap["rows"]) == 4
    # 재호출 없이 스냅숏으로 같은 판정
    rereview(tmp_path / "review" / "youthcenter_raw.json.gz", tmp_path / "again", "2026-10-16")
    assert (tmp_path / "again" / "youthcenter_report.md").read_text(encoding="utf-8").split("### 6.")[0] == md.split("### 6.")[0]


def test_report_refuses_to_write_key(tmp_path):
    key = "TEST-SECRET-KEY-000"
    yc = YouthCenter(api_key=key, http=_http([youth_row(plcyExplnCn=f"이상한 값 {key}")]))
    yc.fetch()
    from collector.review_report import write_review
    with pytest.raises(RuntimeError):
        write_review(yc, tmp_path, include_raw=True, secret=key)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("title, content, kind, stype", [
    ("취업 후 상환 학자금대출_등록금대출", "등록금 대출, 소득 발생 후 상환", "학자금대출", "대출"),
    ("대학생 학자금대출 이자 지원", "한국장학재단을 통해 받은 대출금의 이자 지원", "이자지원", "이자지원"),
    ("학자금 대출 신용회복 지원", "연체자 분할상환 및 채무조정", "신용회복·채무조정", "기타"),
    ("OO시 대학생 장학금", "성적우수 대학생 장학금 100만원 지급", "장학금", "무상지원"),
])
def test_scholarship_aid_kinds_not_shown_as_free_grant(title, content, kind, stype):
    d = dec(plcyNm=title, plcySprtCn=content, plcyExplnCn="대학생 학비 부담 완화")
    assert d.verdict == Y.INCLUDE and d.category == "장학금" and d.aid_kind == kind
    rec = YouthCenter(api_key="K").map_row(youth_row(plcyNm=title, plcySprtCn=content, plcyExplnCn="대학생 학비 부담 완화"))[0].to_record()
    assert rec["support_type"] == stype and rec["flags"]["aid_kind"] == kind
    if kind != "장학금":
        assert any("무상 장학금이 아님" in x["note"] for x in rec["review_notes"])


def test_basis_says_only_what_was_checked():
    d = dec(plcyNm="청년 정착 지원금", plcyExplnCn="청년 정착", plcySprtCn="1인당 월 30만원 지급")
    assert d.verdict == Y.INCLUDE and "정책명·지원목적·지원내용에서 사업화·사업장 비용" in d.basis_text()
    # 지원내용에 '창업'이 대상 설명으로만 나오면 보류하지 않음
    d = dec(plcyNm="청년 정착 지원금", plcyExplnCn="청년 정착", plcySprtCn="1인당 월 30만원 지급, 창업 시 추가")
    assert d.verdict == Y.INCLUDE
    # 지원내용에서 실제 지원 비용이 사업화 자금이면 개인 생활비로 넣지 않음(근거에 지원내용 표기)
    d = dec(plcyNm="청년 정착 지원", plcyExplnCn="청년 정착", plcySprtCn="창업 기업 사업화 자금 최대 2,000만원")
    assert d.verdict == Y.EXCLUDE and d.reason == Y.EX_BUSINESS and "지원내용에 '사업화 자금'" in d.basis_text()


def test_aux_deadline_when_apply_field_empty():
    row = youth_row(plcyNm="충주시 청년 입영지원금", plcyExplnCn="입영하는 청년 복지 증진", aplyYmd="",
                    bizPrdBgngYmd="20250106", bizPrdEndYmd="20251231",
                    plcySprtCn="-지원내용 : 입영지원금 10만원 지원 -지급방법 : 지역화폐카드 충전 "
                               "※입영(소집)일로부터 6개월이 되는 날이 속한 달 말일까지 신청 가능")
    rec = YouthCenter(api_key="K").map_row(row)[0].to_record()
    assert rec["period_kind"] == "사유 발생 후 기한"
    assert rec["apply_start"] is None and rec["apply_end"] is None and rec["period_month_end"] is None
    assert rec["flags"]["period_aux"]["field"] == "plcySprtCn"
    assert "6개월이 되는 날이 속한 달 말일까지" in rec["flags"]["period_aux"]["sentence"]
    assert rec["period_text"].startswith("[지원내용에서 보조 추출]")
    assert "2025" not in rec["period_text"]                      # 사업기간으로 대체하지 않음
    # 기한 문장이 없으면 그대로 '원문 기간 미기재'
    rec = YouthCenter(api_key="K").map_row(youth_row(aplyYmd=""))[0].to_record()
    assert rec["period_kind"] == "원문 기간 미기재" and "period_aux" not in rec["flags"]


def test_compare_before_after(tmp_path):
    import csv
    from collector.review_report import compare
    head = ["판정", "분야(잠정 포함)", "정책번호", "정책명"]
    for name, rows in (("a.csv", [["포함", "생활비", "1", "관광두레"], ["포함", "주거비", "2", "월세"]]),
                       ("b.csv", [["분류 검토", "생활비", "1", "관광두레"], ["포함", "주거비", "2", "월세"]])):
        with open(tmp_path / name, "w", encoding="utf-8-sig", newline="") as f:
            csv.writer(f).writerows([head] + rows)
    r = compare(tmp_path / "a.csv", tmp_path / "b.csv", tmp_path / "c.md")
    assert r["moved"] == 1 and "관광두레" in (tmp_path / "c.md").read_text(encoding="utf-8")


def test_dry_run_summary_splits_new_and_points_to_artifacts(tmp_path):
    from collector.export import export
    from collector.models import now_kst
    from collector.run import run_source
    from collector.store import Store
    from tests.test_pipeline import FakeSource, item
    st = Store(tmp_path / "t.db")
    today = now_kst().date().isoformat()
    gov = FakeSource("gov24", [item("gov24", "1")])
    run_source(st, gov, today, force=False, log=lambda *_: None)                 # 오전 정식 실행의 신규 1건
    yc = FakeSource("youthcenter", [item("youthcenter", str(i), title=f"T{i}") for i in range(20)])
    out = {"youthcenter": run_source(st, yc, today, force=False, log=lambda *_: None)}
    export(st, [gov, yc], today, tmp_path / "out", include_manual=False, outcomes=out, dry_run=True)
    s = (tmp_path / "out" / "summary.md").read_text(encoding="utf-8")
    assert "이번 실행(youthcenter) 20건 · 오늘 전체 21건 (이번 실행 대상이 아닌 출처 1건 포함)" in s
    assert "관리 화면 '오늘 신규'에서 확인" not in s
    assert "review/new_this_run.csv" in s
    rows = (tmp_path / "out" / "review" / "new_this_run.csv").read_text(encoding="utf-8-sig").splitlines()
    assert len(rows) == 21                                                       # 머리글 + 이번 실행 신규 20
