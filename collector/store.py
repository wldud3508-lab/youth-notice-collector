"""SQLite 누적 저장소. 파일 하나(data/notices.db)에 공고·버전 이력·실행 기록을 둔다.

GitHub Actions에서는 이 파일을 저장소의 data 브랜치에 커밋해서 다음 실행으로 넘긴다
(실행 환경은 매번 초기화되므로 임시 파일만으로 유지하지 않는다).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import SOURCE_FIELDS, Item, content_hash, diff, iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS notices (
  id TEXT PRIMARY KEY,
  source_key TEXT NOT NULL,
  source_id TEXT NOT NULL,
  record_json TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  first_seen_at TEXT NOT NULL,
  last_checked_at TEXT NOT NULL,
  last_changed_at TEXT,
  last_batch TEXT NOT NULL,
  UNIQUE (source_key, source_id)
);
CREATE TABLE IF NOT EXISTS versions (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  notice_id TEXT NOT NULL REFERENCES notices(id),
  batch_id TEXT NOT NULL,
  at TEXT NOT NULL,
  note TEXT NOT NULL,
  changes_json TEXT NOT NULL,
  record_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS versions_notice ON versions(notice_id);
-- 출처 원본 응답 전체(화면용으로 자르지 않음). 원본 요약값(digest)이 바뀔 때마다 한 줄씩 쌓인다.
CREATE TABLE IF NOT EXISTS raw_sources (
  notice_id TEXT NOT NULL REFERENCES notices(id),
  digest TEXT NOT NULL,
  digest_version INTEGER NOT NULL,
  fetched_at TEXT NOT NULL,
  batch_id TEXT NOT NULL,
  raw_json TEXT NOT NULL,
  PRIMARY KEY (notice_id, digest)
);
CREATE TABLE IF NOT EXISTS runs (
  batch_id TEXT NOT NULL,
  source_key TEXT NOT NULL,
  attempt INTEGER NOT NULL,
  status TEXT NOT NULL,            -- 성공 / 실패 / 연결 보류 / 미연결
  started_at TEXT NOT NULL,
  finished_at TEXT,
  fetched_raw INTEGER,
  collected INTEGER,
  new INTEGER,
  changed INTEGER,
  skipped_json TEXT,
  error TEXT,
  PRIMARY KEY (batch_id, source_key, attempt)
);
"""


# ------------------------------------------------------------------ 원본 비교 규칙
# 비교에서 아예 제외(변경으로 기록하지 않음). 조회수는 원본 보관 단계에서 이미 뺀다.
RAW_IGNORED = {"조회수"}
RAW_ORDERLESS = {"plcyKywdNm"}   # 값의 순서만 바뀌어 오는 목록(온통청년 키워드)
# 비핵심 메타데이터: 이것만 바뀌면 '원본 메타데이터 변경'(검토 상태 유지)
RAW_META = {"수정일시", "등록일시", "소관기관코드",
            "frstRegDt", "lastMdfcnDt"}   # 온통청년 최초등록·최종수정 일시(필드명 기준, 공식 명세 확인 전)
# 그 밖의 항목(지원대상·선정기준·지원내용·신청기한·신청방법·구비서류·지원조건 코드 등)은 모두 핵심으로 본다.
# 목록에 없는 항목도 중요하지 않다고 추정하지 않는다.

NOTE_NEW = "최초 수집"
NOTE_BACKFILL = "원문 보완(데이터 이전)"            # 원본 전체를 처음 보관 — 공고 변경 아님
NOTE_RULE = "분류 규칙 변경"                        # 원본 같음, 정리 결과만 다름
NOTE_CORE = "중요 원문 변경"                        # 핵심 항목 변경 → 미검토로
NOTE_META = "원본 메타데이터 변경"                  # 수정일시 등만 변경 → 검토 상태 유지
NOTE_UNKNOWN = "원본 변경 — 변경 항목 확인 불가"     # 이전 원본이 없어 비교 불가 → 미검토 + 추가 검토
NOTE_UNVERIFIED = "이전 요약값과 차이 있음 — 이전 원문 미보관으로 변경 항목 확인 불가"
NOTE_DISPLAY = "공고 내용 변경"                     # 원본 요약값 없이 화면용 원문 필드로만 판단(다른 출처·수동 입력)
REVIEW_FLAG_FIELD = "원본 변경 확인"                # needs_review_fields 에 넣는 추가 검토 표시
TRANSIENT_FLAGS = ("clipped_fields", "source_version", "source_mode", "source_cadence")   # 매 실행 갱신, 변경 이력에 남기지 않음


def _cmp_hash(rec: dict) -> str:
    """정리 결과 비교용 해시. 사람이 확인할 때까지 유지하는 추가 검토 표시는 빼고 비교(매번 변경으로 잡히지 않게)."""
    r = dict(rec)
    r["needs_review_fields"] = [f for f in rec.get("needs_review_fields") or [] if f != REVIEW_FLAG_FIELD]
    return content_hash(r)


def raw_diff(old: dict, new: dict) -> list[dict]:
    """원본 응답(목록·상세·지원조건) 항목별 비교. 화면용 600자 제한과 무관하게 원문 전체로 비교한다."""
    out = []
    for sec, label in (("list", "목록"), ("detail", "상세"), ("cond", "지원조건")):
        a, b = (old or {}).get(sec) or {}, (new or {}).get(sec) or {}
        for k in sorted(set(a) | set(b)):
            if k in RAW_IGNORED or a.get(k) == b.get(k):
                continue
            if k in RAW_ORDERLESS and isinstance(a.get(k), str) and isinstance(b.get(k), str) and \
                    sorted(x.strip() for x in a[k].split(",")) == sorted(x.strip() for x in b[k].split(",")):
                continue                     # 쉼표 목록의 순서만 바뀜(온통청년 키워드) — 변경 아님
            out.append({"field": f"원본 {k} [{label}]", "key": k, "before": a.get(k), "after": b.get(k),
                        "kind": "메타데이터" if k in RAW_META else "핵심"})
    return out


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        # 기존 DB에 없는 열·표를 추가(데이터는 그대로 유지)
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(runs)")}
        for col in ("reclassified", "raw_backfilled", "meta_changed", "unverified"):
            if col not in cols:
                self.db.execute(f"ALTER TABLE runs ADD COLUMN {col} INTEGER")
        if "fetch_mode" not in cols:
            self.db.execute("ALTER TABLE runs ADD COLUMN fetch_mode TEXT")
        self.db.execute("CREATE TABLE IF NOT EXISTS migrations (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL, detail TEXT)")
        self.last_backfilled = False
        self.last_unverified = False

    def close(self):
        self.db.commit()
        self.db.close()

    # ------------------------------------------------------------ 공고
    def upsert(self, item: Item, batch_id: str, now) -> str:
        """같은 (출처, 출처 ID)는 한 행만 유지한다. 돌려주는 값:
        'new' | 'changed'(중요 원문 변경·확인 불가·화면 원문 변경) | 'meta_changed' | 'reclassified' | 'same'
        self.last_backfilled: 원본 전체를 처음 보관했는가 / self.last_unverified: 변경 항목을 확인할 수 없었는가"""
        rec = item.to_record()
        h = content_hash(rec)
        t = iso(now)
        self.last_backfilled = self.last_unverified = False
        row = self.db.execute("SELECT * FROM notices WHERE source_key=? AND source_id=?",
                              (item.source_key, item.source_id)).fetchone()
        if row is None:
            rec.update(first_seen_at=t, last_checked_at=t, last_changed_at=None, changed_fields=[])
            self.db.execute("INSERT INTO notices VALUES (?,?,?,?,?,?,?,?,?)",
                            (rec["id"], item.source_key, item.source_id, json.dumps(rec, ensure_ascii=False),
                             h, t, t, None, batch_id))
            self._version(rec["id"], batch_id, t, NOTE_NEW, [], rec)
            self._store_raw(rec["id"], item, t, batch_id)
            return "new"

        nid = row["id"]
        old = json.loads(row["record_json"])
        old_flags = {k: v for k, v in old.get("flags", {}).items() if k != "not_seen_since"}
        new_flags = rec.get("flags", {})
        od, nd = old_flags.get("source_digest"), new_flags.get("source_digest")
        odv, ndv = old_flags.get("digest_version", 1), new_flags.get("digest_version", 1)
        prev_raw = self.raw_of(nid)                       # 직전에 보관한 원본(없으면 None)
        self._store_raw(nid, item, t, batch_id)
        display_changes = diff(old, rec)
        result_changed = bool(display_changes) or _cmp_hash(old) != _cmp_hash(rec)   # 정리 결과(분류·확인 표시 포함)가 달라졌나

        # ---- 어떤 경우인지 판정
        if nd and od and odv == ndv and od == nd:
            kind = "rule" if result_changed else "same"             # 원본 같음
        elif nd and od and odv == ndv:                                # 원본 달라짐
            if prev_raw is not None and item.raw is not None:
                rch = raw_diff(prev_raw, item.raw)
                if any(c["kind"] == "핵심" for c in rch):
                    kind = "core"
                elif rch:
                    kind = "meta"
                else:
                    kind = "rule" if result_changed else "same"     # 비교 제외 항목만 다름
            else:
                kind, rch = "unknown", []
        elif nd:                                                      # 이전 요약값 없음·계산 방식 바뀜 = 데이터 이전
            kind = "display" if any(c["field"] in SOURCE_FIELDS for c in display_changes) else (
                "rule" if result_changed else "same")
            self.last_backfilled = True   # 원본 요약값을 처음 갖추거나 계산 방식이 바뀜 = 데이터 이전(공고 변경 아님)
        else:                                                         # 원본 요약값이 없는 출처
            kind = "display" if any(c["field"] in SOURCE_FIELDS for c in display_changes) else (
                "rule" if result_changed else "same")
        if kind != "core" and kind != "meta":
            rch = []
        if not self.last_backfilled and prev_raw is None and item.raw is not None and kind == "same":
            self.last_backfilled = True                               # 요약값은 같고 원본만 처음 보관

        # ---- 저장
        base_flags = {k: v for k, v in old_flags.items() if k not in TRANSIENT_FLAGS}
        trans = {k: new_flags[k] for k in TRANSIENT_FLAGS if k in new_flags}   # 축약 표시는 매번 새 값으로
        keep = {k: new_flags[k] for k in ("source_digest", "digest_version") if k in new_flags}

        if kind == "same":
            old["last_checked_at"] = t
            old["flags"] = {**base_flags, **keep, **trans}
            self.db.execute("UPDATE notices SET record_json=?, last_checked_at=?, last_batch=? WHERE id=?",
                            (json.dumps(old, ensure_ascii=False), t, batch_id, nid))
            if self.last_backfilled:
                self._version(nid, batch_id, t, NOTE_BACKFILL, [], old)
            return "same"

        rec["first_seen_at"] = old["first_seen_at"]
        rec["last_checked_at"] = t
        rec["flags"] = {**base_flags, **{k: v for k, v in new_flags.items() if k not in TRANSIENT_FLAGS}, **trans}
        changes = display_changes + [{k: c[k] for k in ("field", "before", "after")} for c in rch]

        if kind in ("core", "unknown", "display"):
            note = {"core": NOTE_CORE, "unknown": NOTE_UNKNOWN, "display": NOTE_DISPLAY}[kind]
            rec["last_changed_at"] = t        # 이 실행에서 감지한 시각(공고가 실제로 바뀐 시각은 알 수 없음)
            rec["changed_fields"] = [c["field"] for c in changes] or [note]
            rec["flags"]["review_reset_at"] = t
            rec["flags"]["last_change_kind"] = note
            if kind == "unknown":
                self.last_unverified = True
                rec["needs_review_fields"] = sorted(set(rec["needs_review_fields"]) | {REVIEW_FLAG_FIELD})
                rec["flags"]["source_diff_unverified"] = {"batch": batch_id, "note": NOTE_UNKNOWN}
            ret = "changed"
        elif kind == "meta":
            note = NOTE_META
            rec["last_changed_at"] = old.get("last_changed_at")
            rec["changed_fields"] = old.get("changed_fields", [])
            rec["flags"]["meta_changed_at"] = t
            ret = "meta_changed"
        else:  # rule
            note = f"{NOTE_BACKFILL} · {NOTE_RULE}" if self.last_backfilled else NOTE_RULE
            rec["last_changed_at"] = old.get("last_changed_at")
            rec["changed_fields"] = old.get("changed_fields", [])
            rec["flags"]["rule_changed_at"] = t
            ret = "reclassified"
        if kind != "unknown" and REVIEW_FLAG_FIELD in (old.get("needs_review_fields") or []) and \
                old_flags.get("source_diff_unverified"):
            # 확인 불가 표시는 사람이 확인할 때까지 유지
            rec["needs_review_fields"] = sorted(set(rec["needs_review_fields"]) | {REVIEW_FLAG_FIELD})
        self.db.execute("UPDATE notices SET record_json=?, content_hash=?, last_checked_at=?, last_changed_at=?, last_batch=? WHERE id=?",
                        (json.dumps(rec, ensure_ascii=False), content_hash(rec), t, rec["last_changed_at"], batch_id, nid))
        self._version(nid, batch_id, t, note, changes, rec)
        return ret

    def _store_raw(self, nid: str, item: Item, t: str, batch_id: str) -> None:
        """원본 응답 전체를 보관(같은 요약값이면 다시 넣지 않음)."""
        if not item.raw or not item.source_digest:
            return
        self.db.execute("INSERT OR IGNORE INTO raw_sources VALUES (?,?,?,?,?,?)",
                        (nid, item.source_digest, item.digest_version, t, batch_id,
                         json.dumps(item.raw, ensure_ascii=False, sort_keys=True)))

    def raw_of(self, nid: str) -> dict | None:
        """가장 최근에 보관한 원본."""
        r = self.db.execute("SELECT raw_json FROM raw_sources WHERE notice_id=? ORDER BY fetched_at DESC, rowid DESC LIMIT 1",
                            (nid,)).fetchone()
        return json.loads(r["raw_json"]) if r else None

    # ------------------------------------------------------------ 1회성 데이터 점검
    def migrate_unverified_backfill(self, now) -> int:
        """원본 첫 보관(원문 보완) 때 이전 원본 요약값과 달랐던 공고를 찾아 '확인 불가'로 기록(한 번만 실행).
        이전 원문이 없으므로 변경 내용·변경 시각은 만들지 않는다."""
        name = "unverified_digest_at_first_raw_backfill"
        if self.db.execute("SELECT 1 FROM migrations WHERE name=?", (name,)).fetchone():
            return 0
        t = iso(now)
        found = []
        for nrow in self.db.execute("SELECT id, record_json FROM notices").fetchall():
            nid = nrow["id"]
            vs = self.db.execute("SELECT seq, batch_id, note, record_json FROM versions WHERE notice_id=? ORDER BY seq",
                                 (nid,)).fetchall()
            bf = next((v for v in vs if v["note"].startswith(NOTE_BACKFILL)), None)
            first_raw = self.db.execute("SELECT digest, batch_id FROM raw_sources WHERE notice_id=? ORDER BY fetched_at, rowid LIMIT 1",
                                        (nid,)).fetchone()
            if bf is None or first_raw is None:
                continue
            prev_digest = None
            for v in vs:
                if v["seq"] >= bf["seq"]:
                    break
                prev_digest = json.loads(v["record_json"]).get("flags", {}).get("source_digest") or prev_digest
            if not prev_digest or prev_digest == first_raw["digest"]:
                continue
            rec = json.loads(nrow["record_json"])
            rec["needs_review_fields"] = sorted(set(rec.get("needs_review_fields") or []) | {REVIEW_FLAG_FIELD})
            rec.setdefault("flags", {})["source_diff_unverified"] = {"batch": first_raw["batch_id"], "note": NOTE_UNVERIFIED}
            rec["flags"]["review_reset_at"] = t     # 검토 상태를 미검토로(공고 변경 시각은 기록하지 않음)
            self.db.execute("UPDATE notices SET record_json=?, content_hash=? WHERE id=?",
                            (json.dumps(rec, ensure_ascii=False), content_hash(rec), nid))
            self._version(nid, "데이터 점검", t, NOTE_UNVERIFIED,
                          [{"field": "원본 요약값", "before": prev_digest, "after": first_raw["digest"]}], rec)
            found.append(nid)
        self.db.execute("INSERT INTO migrations VALUES (?,?,?)", (name, t, json.dumps(found)))
        self.db.commit()
        return len(found)

    def mark_unseen(self, source_key: str, seen_ids: set[str], batch_id: str):
        """성공한 수집에서 보이지 않은 공고. 삭제·마감 처리하지 않고 표시만 한다."""
        for row in self.db.execute("SELECT id, source_id, record_json FROM notices WHERE source_key=?", (source_key,)).fetchall():
            if row["source_id"] in seen_ids:
                continue
            rec = json.loads(row["record_json"])
            if "not_seen_since" not in rec.get("flags", {}):
                rec.setdefault("flags", {})["not_seen_since"] = batch_id
                self.db.execute("UPDATE notices SET record_json=? WHERE id=?", (json.dumps(rec, ensure_ascii=False), row["id"]))

    def _version(self, nid, batch_id, t, note, changes, rec):
        self.db.execute("INSERT INTO versions (notice_id, batch_id, at, note, changes_json, record_json) VALUES (?,?,?,?,?,?)",
                        (nid, batch_id, t, note, json.dumps(changes, ensure_ascii=False), json.dumps(rec, ensure_ascii=False)))

    def all_records(self) -> list[dict]:
        out = []
        for row in self.db.execute("SELECT * FROM notices ORDER BY first_seen_at, id"):
            rec = json.loads(row["record_json"])
            rec["history"] = [
                {"at": v["at"], "batch": v["batch_id"], "note": v["note"], "changes": json.loads(v["changes_json"])}
                for v in self.db.execute("SELECT * FROM versions WHERE notice_id=? ORDER BY seq DESC", (row["id"],))
            ]
            out.append(rec)
        return out

    # ------------------------------------------------------------ 실행 기록
    def attempts(self, batch_id: str, source_key: str) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM runs WHERE batch_id=? AND source_key=? ORDER BY attempt",
                               (batch_id, source_key)).fetchall()

    def start_run(self, batch_id: str, source_key: str, now) -> int:
        n = len(self.attempts(batch_id, source_key)) + 1
        self.db.execute("INSERT INTO runs (batch_id, source_key, attempt, status, started_at) VALUES (?,?,?,?,?)",
                        (batch_id, source_key, n, "실행 중", iso(now)))
        self.db.commit()
        return n

    def finish_run(self, batch_id, source_key, attempt, now, *, status, fetched_raw=None, collected=None,
                   new=None, changed=None, skipped=None, error=None, reclassified=None, raw_backfilled=None,
                   meta_changed=None, unverified=None, fetch_mode=None):
        self.db.execute("""UPDATE runs SET status=?, finished_at=?, fetched_raw=?, collected=?, new=?, changed=?,
                           reclassified=?, raw_backfilled=?, meta_changed=?, unverified=?, fetch_mode=?, skipped_json=?, error=?
                           WHERE batch_id=? AND source_key=? AND attempt=?""",
                        (status, iso(now), fetched_raw, collected, new, changed, reclassified, raw_backfilled,
                         meta_changed, unverified, fetch_mode,
                         json.dumps(skipped, ensure_ascii=False) if skipped else None, error,
                         batch_id, source_key, attempt))
        self.db.commit()

    def latest_run(self, batch_id: str, source_key: str):
        rows = self.attempts(batch_id, source_key)
        return rows[-1] if rows else None

    def last_success(self, source_key: str, real_only: bool = False) -> str | None:
        """real_only: 시험용 CSV 실행은 빼고 실제 출처에서 받은 성공만."""
        extra = " AND (fetch_mode IS NULL OR fetch_mode != '공식 CSV')" if real_only else ""
        r = self.db.execute(f"SELECT MAX(finished_at) AS t FROM runs WHERE source_key=? AND status='성공'{extra}", (source_key,)).fetchone()
        return r["t"] if r else None

    def latest_batch(self) -> str | None:
        r = self.db.execute("SELECT MAX(batch_id) AS b FROM runs").fetchone()
        return r["b"] if r else None
