from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import time


class Store:
    def __init__(self, home: Path):
        self.home = Path(home)
        self.home.mkdir(parents=True, exist_ok=True)
        self.path = self.home / "jobs.sqlite3"
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS assets (
                    id TEXT PRIMARY KEY, relative_path TEXT NOT NULL,
                    size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
                    observed_at REAL NOT NULL, stable INTEGER NOT NULL DEFAULT 0,
                    present INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, dedupe_key TEXT UNIQUE NOT NULL,
                    asset_id TEXT NOT NULL, relative_path TEXT NOT NULL,
                    source_sha TEXT NOT NULL, source_size INTEGER NOT NULL,
                    source_mtime_ns INTEGER NOT NULL, params TEXT NOT NULL,
                    requested_at REAL NOT NULL, status TEXT NOT NULL,
                    progress TEXT NOT NULL DEFAULT '{}', error TEXT,
                    output_path TEXT, output_sha TEXT,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS asset_decisions (
                    asset_id TEXT PRIMARY KEY, size INTEGER NOT NULL,
                    mtime_ns INTEGER NOT NULL, transcribe INTEGER NOT NULL, updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
                CREATE INDEX IF NOT EXISTS jobs_asset_completion ON jobs(asset_id,status,source_size,source_mtime_ns);
                CREATE TABLE IF NOT EXISTS renames (
                    id INTEGER PRIMARY KEY, job_id TEXT NOT NULL UNIQUE,
                    asset_id TEXT NOT NULL, source_sha TEXT NOT NULL,
                    original_path TEXT NOT NULL, previous_path TEXT NOT NULL,
                    renamed_path TEXT NOT NULL, mtime_ns INTEGER NOT NULL,
                    size INTEGER NOT NULL, status TEXT NOT NULL
                );
            """)
            if 'display_title' not in {row[1] for row in db.execute('PRAGMA table_info(jobs)')}:
                db.execute('ALTER TABLE jobs ADD COLUMN display_title TEXT')
            if 'completed_at' not in {row[1] for row in db.execute('PRAGMA table_info(jobs)')}:
                db.execute('ALTER TABLE jobs ADD COLUMN completed_at REAL')
                # Earlier versions retained only the last update time. Infer once,
                # then keep completion separate from subsequent title edits.
            db.execute("UPDATE jobs SET completed_at=updated_at WHERE status='completed' AND completed_at IS NULL")

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=30000")
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def meta(self, key, value=None):
        with self.db() as db:
            if value is not None:
                db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (key, json.dumps(value, ensure_ascii=False)))
            row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def assets(self):
        with self.db() as db:
            return [dict(x) for x in db.execute("""SELECT assets.*, CAST(assets.mtime_ns AS TEXT) AS plan_mtime_ns, EXISTS(
                SELECT 1 FROM jobs WHERE jobs.asset_id=assets.id AND jobs.status='completed'
                AND jobs.source_size=assets.size AND jobs.source_mtime_ns=assets.mtime_ns
                ) AS transcribed, EXISTS(
                SELECT 1 FROM asset_decisions e WHERE e.asset_id=assets.id
                AND e.size=assets.size AND e.mtime_ns=assets.mtime_ns AND e.transcribe=0
                ) AS excluded, EXISTS(
                SELECT 1 FROM asset_decisions e WHERE e.asset_id=assets.id
                AND e.size=assets.size AND e.mtime_ns=assets.mtime_ns AND e.transcribe=1
                ) AS candidate FROM assets WHERE present=1 ORDER BY mtime_ns DESC""")]

    def asset(self, identity):
        with self.db() as db:
            row = db.execute("""SELECT assets.*, CAST(assets.mtime_ns AS TEXT) AS plan_mtime_ns, EXISTS(
                SELECT 1 FROM asset_decisions e WHERE e.asset_id=assets.id
                AND e.size=assets.size AND e.mtime_ns=assets.mtime_ns AND e.transcribe=0
                ) AS excluded, EXISTS(
                SELECT 1 FROM asset_decisions e WHERE e.asset_id=assets.id
                AND e.size=assets.size AND e.mtime_ns=assets.mtime_ns AND e.transcribe=1
                ) AS candidate FROM assets WHERE id=?""", (identity,)).fetchone()
        if not row or not row["present"]:
            raise ValueError("목록에서 파일을 다시 확인해 주세요.")
        return dict(row)

    def set_candidate(self, identity, transcribe, size, mtime_ns):
        if isinstance(mtime_ns,str) and mtime_ns.isdecimal() and len(mtime_ns)<=20:mtime_ns=int(mtime_ns)
        if type(transcribe) is not bool or type(size) is not int or type(mtime_ns) is not int:
            raise ValueError('전사 예정 설정과 파일 정보를 확인하세요.')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            asset=db.execute('SELECT * FROM assets WHERE id=? AND present=1',(identity,)).fetchone()
            if not asset or (asset['size'],asset['mtime_ns'])!=(size,mtime_ns):
                raise ValueError('목록의 파일 정보가 바뀌었습니다. 새로고침 후 다시 선택하세요.')
            active=db.execute("""SELECT 1 FROM jobs WHERE asset_id=? AND (
                status IN ('queued','queued_summary','running','saving','waiting_ai') OR
                (status='completed' AND source_size=? AND source_mtime_ns=?)) LIMIT 1""",
                (identity,size,mtime_ns)).fetchone()
            if active:raise ValueError('전사 중이거나 완료된 녹음입니다. 진행 중이면 먼저 작업을 취소하세요.')
            db.execute('INSERT OR REPLACE INTO asset_decisions VALUES(?,?,?,?,?)',
                       (identity,size,mtime_ns,int(transcribe),time.time()))

    def jobs(self):
        with self.db() as db:
            rows = db.execute("SELECT * FROM jobs ORDER BY requested_at DESC LIMIT 300").fetchall()
        return [self.unpack(x) for x in rows]

    @staticmethod
    def unpack(row):
        if row is None:
            return None
        value = dict(row)
        value["params"] = json.loads(value["params"])
        value["progress"] = json.loads(value["progress"])
        return value

    def job(self, identity):
        with self.db() as db:
            job = self.unpack(db.execute("SELECT * FROM jobs WHERE id=?", (identity,)).fetchone())
            if job:
                rename = db.execute("SELECT * FROM renames WHERE asset_id=? AND source_sha=? ORDER BY id DESC LIMIT 1",
                                    (job["asset_id"], job["source_sha"])).fetchone()
                if rename:
                    new = Path(job["params"]["nas_root"]) / rename["renamed_path"]
                    old = Path(job["params"]["nas_root"]) / rename["previous_path"]
                    if rename["status"] == "completed" or (new.is_file() and not old.exists()):
                        job["resolved_relative_path"] = rename["renamed_path"]
            return job

    def original_name(self, asset):
        with self.db() as db:
            row = db.execute("SELECT original_path FROM renames WHERE asset_id=? ORDER BY id LIMIT 1", (asset["id"],)).fetchone()
        return row[0] if row else asset["relative_path"]

    def rename_aliases(self):
        with self.db() as db:
            return {r["renamed_path"]: r["asset_id"] for r in db.execute("SELECT asset_id,renamed_path FROM renames ORDER BY id")}

    def change_job(self, identity, **fields):
        allowed = {"status", "progress", "error", "output_path", "output_sha", "cancel_requested", "display_title"}
        if not fields or not set(fields) <= allowed:
            raise ValueError("잘못된 작업 변경입니다.")
        if "progress" in fields:
            fields["progress"] = json.dumps(fields["progress"], ensure_ascii=False)
        fields["updated_at"] = time.time()
        with self.db() as db:
            db.execute("UPDATE jobs SET " + ",".join(k + "=?" for k in fields) + " WHERE id=?",
                       [*fields.values(), identity])
            if fields.get('status') == 'completed':
                db.execute('UPDATE jobs SET completed_at=COALESCE(completed_at,?) WHERE id=?',
                           (fields['updated_at'], identity))

    def recover(self):
        # A stored, explicit request survives a service restart. Completed chunks remain.
        with self.db() as db:
            db.execute("UPDATE jobs SET status=CASE WHEN cancel_requested=1 THEN 'cancelled' ELSE 'queued' END, "
                       "updated_at=? WHERE status IN ('running','saving')", (time.time(),))

    def busy(self):
        with self.db() as db:
            return bool(db.execute("SELECT 1 FROM jobs WHERE status IN ('queued','queued_summary','running','saving') LIMIT 1").fetchone())
