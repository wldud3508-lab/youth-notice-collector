"""SQLite 누적 저장소. 파일 하나(data/notices.db)에 공고·버전 이력·실행 기록을 둔다.

GitHub Actions에서는 이 파일을 저장소의 data 브랜치에 커밋해서 다음 실행으로 넘긴다
(실행 환경은 매번 초기화되므로 임시 파일만으로 유지하지 않는다).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import SOURCE_FIELDS, Item, content_hash, diff, iso

NOTE_BACKFILL = "원문 보완(데이터 이전)"   # 원본 전체를 처음 보관 — 공고 변경 아님

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


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        # 기존 DB에 없는 열을 추가(데이터는 그대로 유지)
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(runs)")}
        for col in ("reclassified", "raw_backfilled"):
            if col not in cols:
                self.db.execute(f"ALTER TABLE runs ADD COLUMN {col} INTEGER")
        self.last_backfilled = False

    def close(self):
        self.db.commit()
        self.db.close()

    # ------------------------------------------------------------ 공고
    def upsert(self, item: Item, batch_id: str, now) -> str:
        """'new' | 'changed'(공고 원문 변경) | 'reclassified'(분류 규칙 변경·원문 보완) | 'same'.
        같은 (출처, 출처 ID)는 한 행만 유지한다. self.last_backfilled: 기존 공고의 원본 전체를 처음 보관했는가."""
        rec = item.to_record()
        h = content_hash(rec)
        t = iso(now)
        self.last_backfilled = False
        row = self.db.execute("SELECT * FROM notices WHERE source_key=? AND source_id=?",
                              (item.source_key, item.source_id)).fetchone()
        if row is None:
            rec.update(first_seen_at=t, last_checked_at=t, last_changed_at=None, changed_fields=[])
            self.db.execute("INSERT INTO notices VALUES (?,?,?,?,?,?,?,?,?)",
                            (rec["id"], item.source_key, item.source_id, json.dumps(rec, ensure_ascii=False),
                             h, t, t, None, batch_id))
            self._version(rec["id"], batch_id, t, "최초 수집", [], rec)
            self._store_raw(rec["id"], item, t, batch_id)
            return "new"
        nid = row["id"]
        old = json.loads(row["record_json"])
        old_flags = {k: v for k, v in old.get("flags", {}).items() if k != "not_seen_since"}
        new_flags = rec.get("flags", {})
        od, nd = old_flags.get("source_digest"), new_flags.get("source_digest")
        odv, ndv = old_flags.get("digest_version", 1), new_flags.get("digest_version", 1)
        # 이전 원본 요약값이 없거나 계산 방식이 바뀌었으면 '데이터 이전' — 요약값끼리 비교할 수 없다
        migrating = bool(nd) and (not od or odv != ndv)
        backfill = self._store_raw(nid, item, t, batch_id)   # 기존 공고의 원본 전체를 처음 보관하는가

        if row["content_hash"] == h:
            old["last_checked_at"] = t
            old["flags"] = {**old_flags, **{k: v for k, v in new_flags.items() if k in ("source_digest", "digest_version")}}
            self.db.execute("UPDATE notices SET record_json=?, last_checked_at=?, last_batch=? WHERE id=?",
                            (json.dumps(old, ensure_ascii=False), t, batch_id, nid))
            if backfill or migrating:
                self.last_backfilled = True
                self._version(nid, batch_id, t, NOTE_BACKFILL, [], old)
            return "same"

        changes = diff(old, rec)
        if od and nd and not migrating:
            official = od != nd                       # 같은 방식으로 계산한 원본 요약값 비교
        else:
            # 데이터 이전 중: 화면용 원문 필드(같은 규칙으로 잘라 저장)끼리만 비교할 수 있다
            official = any(c["field"] in SOURCE_FIELDS for c in changes)
        if official:
            rec.update(first_seen_at=old["first_seen_at"], last_checked_at=t, last_changed_at=t,
                       changed_fields=[c["field"] for c in changes] or ["needs_review_fields"])
            rec["flags"] = {**old_flags, **new_flags,
                            "review_reset_at": t}   # 공고 내용이 바뀌면 화면의 검토 상태를 미검토로 되돌림
            self.db.execute("UPDATE notices SET record_json=?, content_hash=?, last_checked_at=?, last_changed_at=?, last_batch=? WHERE id=?",
                            (json.dumps(rec, ensure_ascii=False), h, t, t, batch_id, nid))
            self._version(nid, batch_id, t, "공고 내용 변경", changes, rec)
            return "changed"
        # 원본은 같고 결과만 다름 → '분류 규칙 변경'(데이터 이전이 겹치면 함께 표기). 공고 변경 시각·검토 상태는 그대로
        rec.update(first_seen_at=old["first_seen_at"], last_checked_at=t,
                   last_changed_at=old.get("last_changed_at"), changed_fields=old.get("changed_fields", []))
        rec["flags"] = {**old_flags, **new_flags, "rule_changed_at": t}
        self.db.execute("UPDATE notices SET record_json=?, content_hash=?, last_checked_at=?, last_batch=? WHERE id=?",
                        (json.dumps(rec, ensure_ascii=False), h, t, batch_id, nid))
        note = "분류 규칙 변경"
        if backfill or migrating:
            self.last_backfilled = True
            note = f"{NOTE_BACKFILL} · 분류 규칙 변경"
        self._version(nid, batch_id, t, note, changes, rec)
        return "reclassified"

    def _store_raw(self, nid: str, item: Item, t: str, batch_id: str) -> bool:
        """원본 응답 전체를 보관. 기존 공고에 원본이 하나도 없다가 처음 들어가면 True(원문 보완)."""
        if not item.raw or not item.source_digest:
            return False
        had = self.db.execute("SELECT 1 FROM raw_sources WHERE notice_id=? LIMIT 1", (nid,)).fetchone()
        self.db.execute("INSERT OR IGNORE INTO raw_sources VALUES (?,?,?,?,?,?)",
                        (nid, item.source_digest, item.digest_version, t, batch_id,
                         json.dumps(item.raw, ensure_ascii=False, sort_keys=True)))
        first_version = self.db.execute("SELECT COUNT(*) FROM versions WHERE notice_id=?", (nid,)).fetchone()[0] > 0
        return had is None and first_version

    def raw_of(self, nid: str) -> dict | None:
        r = self.db.execute("SELECT raw_json FROM raw_sources WHERE notice_id=? ORDER BY fetched_at DESC LIMIT 1", (nid,)).fetchone()
        return json.loads(r["raw_json"]) if r else None

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
                   new=None, changed=None, skipped=None, error=None, reclassified=None, raw_backfilled=None):
        self.db.execute("""UPDATE runs SET status=?, finished_at=?, fetched_raw=?, collected=?, new=?, changed=?,
                           reclassified=?, raw_backfilled=?, skipped_json=?, error=? WHERE batch_id=? AND source_key=? AND attempt=?""",
                        (status, iso(now), fetched_raw, collected, new, changed, reclassified, raw_backfilled,
                         json.dumps(skipped, ensure_ascii=False) if skipped else None, error,
                         batch_id, source_key, attempt))
        self.db.commit()

    def latest_run(self, batch_id: str, source_key: str):
        rows = self.attempts(batch_id, source_key)
        return rows[-1] if rows else None

    def last_success(self, source_key: str) -> str | None:
        r = self.db.execute("SELECT MAX(finished_at) AS t FROM runs WHERE source_key=? AND status='성공'", (source_key,)).fetchone()
        return r["t"] if r else None

    def latest_batch(self) -> str | None:
        r = self.db.execute("SELECT MAX(batch_id) AS b FROM runs").fetchone()
        return r["b"] if r else None
