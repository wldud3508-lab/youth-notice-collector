# 청년 공고 수집기

공모전·장학금·생활비·주거비 공고를 하루 한 번 모아 `latest.json`으로 정리하고, 확정된 관리 화면(`web/index.html`)에서 보게 합니다.

## 구성 한눈에 보기

```
GitHub Actions (매일 09:00 KST, 컴퓨터 꺼져 있어도 실행)
  └ python -m collector run
      ├ 출처별 수집 (정부24·온통청년·한국장학재단 API, 캠퍼스칼라 RSS, 수동 입력)
      ├ 누적 DB(store/notices.db) 갱신: 신규·변경 감지, 변경 이력 저장
      └ 결과 파일: latest.json, CSV 3종, summary.md
  ├ 누적 DB와 결과 파일을 저장소의 data 브랜치에 커밋  ← 실행 환경이 초기화돼도 유지
  └ 관리 화면 + data/latest.json 을 GitHub Pages에 배포 ← 화면이 열릴 때 자동으로 읽음
```

| 경로 | 내용 |
|---|---|
| `schema/notice-data.schema.json` | 화면과 수집기가 공유하는 JSON 스키마 ({meta, sources, notices}) |
| `collector/` | 수집기. `sources/`에 출처별 모듈, `store.py` 누적 DB, `export.py` 결과 파일 |
| `web/index.html` | 관리 화면. 기본은 `data/latest.json`(실제 결과)만 읽음 |
| `web/sample/sample-data.json` | 샘플 19건. 주소 끝에 `#sample`을 붙일 때만 읽음 |
| `manual/notices.example.json` | 자동수집 보류 출처의 공고를 직접 넣는 양식 |
| `docs/SOURCES.md` | 출처 조사표 |
| `.github/workflows/collect.yml` | 예약 실행 |

## 처음 설정하기 (한 번만)

### 1. 인증키 받기
1. **공공데이터포털**(data.go.kr) 가입 → 로그인
2. 검색창에 `15113968` → "행정안전부_대한민국 공공서비스(혜택) 정보" → **활용신청** (목적: 개인 연구 등)
3. (장학금) `15028252` "한국장학재단_학자금지원정보(대학생)" → 활용신청. 승인 후 마이페이지 → 해당 API의 Swagger 화면에서 요청 경로(`/api/15028252/v1/uddi:...`)를 복사
4. 마이페이지 → 일반 인증키(**Decoding**)를 복사
5. **온통청년**(youthcenter.go.kr) 로그인 → Open API 메뉴에서 청년정책 API 인증키 신청 (메뉴 위치는 사이트 개편에 따라 다를 수 있음)

> 승인까지 시간이 걸릴 수 있습니다. 키를 받기 전에도 수집기는 돌아가며, 해당 출처는 '미연결'로 표시됩니다(실패나 0건으로 표시하지 않음).

### 2. GitHub 저장소 만들기
1. github.com 가입 → 오른쪽 위 **+ → New repository**
2. 공개(Public) 저장소를 권장합니다. GitHub Free에서 Pages는 공개 저장소만 됩니다(비공개 저장소는 GitHub Pro 필요). 공개 저장소면 수집된 공고 데이터(공개 정보)도 공개되며, 인증키는 Secrets에 있어 공개되지 않습니다.
3. 이 폴더 전체를 올립니다(웹에서 **Add file → Upload files** 또는 `git push`).

### 3. 비밀값·설정 넣기
저장소 **Settings → Secrets and variables → Actions**
- **Secrets** 탭: `DATA_GO_KR_SERVICE_KEY`, `YOUTHCENTER_API_KEY`
- **Variables** 탭: `KOSAF_ODCLOUD_PATH`(1-3에서 복사한 경로), `CAMPUSCHOLAR_CONFIRMED`(campuscholar.com이 맞으면 `1`)

### 4. Pages 켜기
**Settings → Pages → Build and deployment → Source: GitHub Actions**

### 5. 첫 실행
**Actions 탭 → 매일 공고 수집 → Run workflow**. 끝나면 실행 화면 아래 **Summary**에 출처별 결과표가 나오고,
`https://<아이디>.github.io/<저장소>/` 에서 관리 화면이 열립니다.

## 매일 운영
- 예약: 매일 00:00 UTC(한국 09:00). GitHub의 예약 실행은 정시 보장이 없어 혼잡하면 늦어지거나 드물게 건너뜁니다. 건너뛰어도 다음 실행이 마지막 성공 이후 공고를 다시 확인합니다(정부24·온통청년은 전체 목록 재조회, 캠퍼스칼라는 지난 성공 시점까지 RSS 페이지를 거슬러 조회).
- 시각 변경: `.github/workflows/collect.yml`의 `cron: "0 0 * * *"` (UTC 기준. 한국 시각 − 9시간)
- 지금 수집: 관리 화면의 **지금 수집** 버튼 → GitHub Actions 화면에서 **Run workflow**
- 공개 저장소에서 60일 동안 저장소 활동이 없으면 GitHub가 예약 실행을 자동으로 끕니다. 무엇이 활동으로 인정되는지 GitHub 문서에 정의가 없으므로, 비활성 알림 메일이 오면 Actions 탭에서 다시 켜 주세요.
- 비용: 공개 저장소는 Actions·Pages 무료. 하루 1회 실행은 수 분 수준입니다.

## 같은 날 다시 실행하면
배치 ID는 한국 날짜입니다. 오늘 이미 성공한 출처는 건너뛰고 실패한 출처만 다시 시도합니다(하루 최대 3회). 같은 공고는 (출처, 출처 공고 ID)로 한 번만 저장됩니다.

## 수동 입력 (링커리어·위비티 등)
`manual/notices.example.json`을 `manual/notices.json`으로 복사해 공고를 적고 커밋하면 다음 실행에 '수동 입력' 출처로 들어갑니다.

## 로컬에서 실행 (선택)
```bash
pip install -r requirements-dev.txt
cp .env.example .env      # 키 입력
python -m pytest          # 테스트
python -m collector run   # 결과: data/notices.db, data/public/
```

## 정확성 규칙(구현된 것)
- 게시일(`posted_at`)과 신청 시작일(`apply_start`)을 따로 저장. 신청기간은 신청기간 원문에서만 뽑음
- 금액은 원문 표현 그대로(`월 최대 20만 원`) + 기간 단위·한정어 분리. 금액이 여러 개면 확인 필요
- 대출·이자지원·무상지원 구분. 대출을 지원금으로 표시하지 않음
- 신청기간이 여러 개거나 해석이 불확실하면 날짜를 비우고 원문 보존 + 확인 필요
- '만' 표기 없는 나이는 확정하지 않음
- 목록에서 사라진 공고는 삭제·마감 처리하지 않고 `flags.not_seen_since`만 표시
- 내용이 바뀌면 이전 버전과 변경 내역을 저장하고 화면의 검토 상태를 미검토로 되돌림
- 다른 출처의 같은 공고는 기관·제목·신청기간·신청 링크가 모두 맞을 때만 병합, 제목만 같으면 '중복 후보'
- AI 요약 없음. 추후 AI 추출은 `collector/textutil.py`의 규칙 함수 자리에 별도 모듈로 연결
