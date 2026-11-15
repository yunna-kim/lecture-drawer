from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import threading
import time

from .catalog import scan, approved_source
from .config import ROOT, load_settings
from .output import publish
from .source_rename import rename_selected
from .paragraphs import group_segments
from .gpu_schedule import SummaryDeferred


class WaitingForAI(ValueError):
    pass


class Service:
    def __init__(self, store):
        self.store = store
        self.home = store.home
        self.stop_event = threading.Event()
        self.scan_lock = threading.Lock()
        self.process = None

    def scan(self):
        if not self.scan_lock.acquire(blocking=False):
            return self.store.meta("catalog") or {"connected": False, "scanning": True}
        try:
            return scan(self.store, load_settings(self.home))
        finally:
            self.scan_lock.release()

    def start(self):
        from .workspaces import require_team_allowed
        require_team_allowed(load_settings(self.home))
        self.store.recover()
        self.threads = [threading.Thread(target=self.catalog_loop, daemon=True),
                        threading.Thread(target=self.worker_loop, daemon=True)]
        for thread in self.threads:
            thread.start()

    def close(self):
        self.stop_event.set()
        if self.process and self.process.poll() is None:
            self.stop_child()

    def stop_child(self):
        child = self.process
        if child and child.poll() is None:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(child.pid), "/T", "/F"],
                               capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                child.terminate()
            child.wait(timeout=15)

    def catalog_loop(self):
        while not self.stop_event.is_set():
            try:
                self.scan()
            except Exception:
                self.store.meta("catalog", {"connected": False, "error": "목록 갱신 실패. NAS 연결을 확인하세요."})
            self.stop_event.wait(load_settings(self.home)["scan_seconds"])

    def worker_loop(self):
        while not self.stop_event.is_set():
            with self.store.db() as db:
                row = db.execute("SELECT id FROM jobs WHERE status IN ('queued','queued_summary') AND cancel_requested=0 ORDER BY CASE status WHEN 'queued' THEN 0 ELSE 1 END,requested_at LIMIT 1").fetchone()
            if row:
                try:
                    self.process_job(row["id"])
                except SummaryDeferred as error:
                    self.store.change_job(row['id'],status='queued_summary',progress={'message':str(error)})
                except WaitingForAI as error:
                    self.store.change_job(row["id"], status="waiting_ai", error=str(error)[:1000])
                except Exception as error:
                    self.store.change_job(row["id"], status="failed", error=str(error)[:1000])
            else:
                self.stop_event.wait(1)

    def process_job(self, identity):
        from .workspaces import require_team_allowed
        require_team_allowed(load_settings(self.home))
        job = self.store.job(identity)
        if not job or job["cancel_requested"] or job["status"] not in ("queued","queued_summary"):
            return
        self.store.change_job(identity, status="running", error=None,
                              progress={"message": "선택한 원음과 모델을 확인하고 있습니다."})
        work = self.home / "work" / identity
        work.mkdir(parents=True, exist_ok=True)
        for name in ("failure.json", "progress.json", "note-result.json"):
            (work / name).unlink(missing_ok=True)
        approved_source(job, {"nas_root": job["params"]["nas_root"]})
        result = work / "result.json"
        if not result.exists():
            if not self.execute(identity, "app.engine_runner", work):
                return
        value = json.loads(result.read_text("utf-8"))
        if value["source_sha"] != job["source_sha"] or value["dedupe_key"] != job["dedupe_key"]:
            raise ValueError("전사 결과가 선택한 요청과 다릅니다. 저장을 중단했습니다.")
        if job['params'].get('note_ai',{}).get('provider')=='ollama':
            others=[j for j in self.store.jobs() if j['id']!=identity and j['status']=='queued'
                    and not (self.home/'work'/j['id']/'result.json').exists()]
            if others:raise SummaryDeferred('음성 전사 우선 · 전사 원문을 보존하고 로컬 요약을 대기합니다.')
        self.store.change_job(identity, status="saving", progress={"fraction": 1, "message": "선택한 템플릿·제목을 준비하고 있습니다."})
        if not self.execute(identity, "app.note_runner", work):
            return
        note = json.loads((work / "note-result.json").read_text("utf-8"))
        if note["dedupe_key"] != job["dedupe_key"]:
            raise ValueError("정리 결과가 현재 요청과 다릅니다.")
        fresh = rename_selected(self.store, identity, note.get("title"))
        publish(self.store, fresh, value["segments"], value["versions"], note=note)

    def execute(self, identity, module, work):
        job = self.store.job(identity)
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        # No output containing private names/transcripts is sent to shared console logs.
        with (work / (module.rsplit(".", 1)[-1] + ".log")).open("wb") as log:
            self.process = subprocess.Popen(
                [job["params"]["runtime_python"], "-X", "utf8", "-m", module,
                 "--job", identity, "--state", str(self.home)], cwd=ROOT,
                stdout=log, stderr=log, creationflags=flags, env=env)
            previous = None
            while self.process.poll() is None:
                fresh = self.store.job(identity)
                if fresh["cancel_requested"] or self.stop_event.is_set():
                    self.stop_child()
                    self.store.change_job(identity, status="cancelled" if fresh["cancel_requested"] else "queued")
                    return False
                progress_file = work / "progress.json"
                if progress_file.exists():
                    try:
                        progress = json.loads(progress_file.read_text("utf-8"))
                        if progress != previous:
                            self.store.change_job(identity, progress=progress)
                            previous = progress
                    except (OSError, json.JSONDecodeError):
                        pass
                self.stop_event.wait(0.5)
            code = self.process.returncode
        if self.store.job(identity)["cancel_requested"]:
            self.store.change_job(identity, status="cancelled")
            return False
        if self.stop_event.is_set():
            self.store.change_job(identity, status="queued")
            return False
        expected = work / ("result.json" if module == "app.engine_runner" else "note-result.json")
        if code or not expected.exists():
            fail = work / "failure.json"
            failure = json.loads(fail.read_text("utf-8")) if fail.exists() else {}
            message = failure.get("message", "로컬 전사 실행이 중단됐습니다. 환경 점검 후 재시도하세요.")
            if failure.get('defer_summary'):raise SummaryDeferred(message)
            if failure.get("waiting_ai"):
                raise WaitingForAI(message)
            raise ValueError(message)
        return True

    def result(self, identity):
        job = self.store.job(identity)
        if not job:
            raise ValueError("작업을 찾을 수 없습니다.")
        path = self.home / "work" / identity / "result.json"
        value = json.loads(path.read_text("utf-8")) if path.exists() else {"segments": []}
        return {**value, "paragraphs": group_segments(value["segments"])}
