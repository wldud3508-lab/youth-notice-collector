"""SQLite 누적 저장소. 파일 하나(data/notices.db)에 공고·버전 이력·실행 기록을 둔다.

GitHub Actions에서는 이 파일을 저장소의 data 브랜치에 커밋해서 다음 실행으로 넘긴다
(실행 환경은 매번 초기화되므로 임시 파일만으로 유지하지 않는다).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import Item, content_hash, diff, iso

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

    def close(self):
        self.db.commit()
        self.db.close()

    # ------------------------------------------------------------ 공고
    def upsert(self, item: Item, batch_id: str, now) -> str:
        """'new' | 'changed' | 'same'. 같은 (출처, 출처 ID)는 한 행만 유지한다."""
        rec = item.to_record()
        h = content_hash(rec)
        t = iso(now)
        row = self.db.execute("SELECT * FROM notices WHERE source_key=? AND source_id=?",
                              (item.source_key, item.source_id)).fetchone()
        if row is None:
            rec.update(first_seen_at=t, last_checked_at=t, last_changed_at=None, changed_fields=[])
            self.db.execute("INSERT INTO notices VALUES (?,?,?,?,?,?,?,?,?)",
                            (rec["id"], item.source_key, item.source_id, json.dumps(rec, ensure_ascii=False),
                             h, t, t, None, batch_id))
            self._version(rec["id"], batch_id, t, "최초 수집", [], rec)
            return "new"
        old = json.loads(row["record_json"])
        if row["content_hash"] == h:
            old["last_checked_at"] = t
            old.get("flags", {}).pop("not_seen_since", None)
            self.db.execute("UPDATE notices SET record_json=?, last_checked_at=?, last_batch=? WHERE id=?",
                            (json.dumps(old, ensure_ascii=False), t, batch_id, row["id"]))
            return "same"
        changes = diff(old, rec)
        rec.update(first_seen_at=old["first_seen_at"], last_checked_at=t, last_changed_at=t,
                   changed_fields=[c["field"] for c in changes] or ["needs_review_fields"])
        rec["flags"] = {**{k: v for k, v in old.get("flags", {}).items() if k != "not_seen_since"},
                        "review_reset_at": t}   # 내용이 바뀌면 화면의 검토 상태를 미검토로 되돌림
        self.db.execute("UPDATE notices SET record_json=?, content_hash=?, last_checked_at=?, last_changed_at=?, last_batch=? WHERE id=?",
                        (json.dumps(rec, ensure_ascii=False), h, t, t, batch_id, row["id"]))
        self._version(row["id"], batch_id, t, "내용 변경 감지", changes, rec)
        return "changed"

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
                   new=None, changed=None, skipped=None, error=None):
        self.db.execute("""UPDATE runs SET status=?, finished_at=?, fetched_raw=?, collected=?, new=?, changed=?,
                           skipped_json=?, error=? WHERE batch_id=? AND source_key=? AND attempt=?""",
                        (status, iso(now), fetched_raw, collected, new, changed,
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
