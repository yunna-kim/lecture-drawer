from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import time
import uuid

from .catalog import safe_audio, digest
from .note_templates import freeze


def enqueue(store, settings, selections, profile_id=None):
    from .workspaces import require_team_allowed
    require_team_allowed(settings)
    if not isinstance(selections, list) or not 1 <= len(selections) <= 20:
        raise ValueError("강의 파일을 1~20개 선택해 주세요.")
    profile_id = profile_id or settings["active_profile"]
    if profile_id not in settings["profiles"]:
        raise ValueError("등록된 로컬 모델을 먼저 준비하세요.")
    profile = settings["profiles"][profile_id]
    prepared = []
    # Validate the full batch before any request is committed.
    for selection in selections:
        if not isinstance(selection, dict):
            raise ValueError("선택한 파일 정보를 확인하세요.")
        asset = store.asset(selection.get("asset_id"))
        if asset.get('excluded'):
            raise ValueError('전사 예정에서 뺀 녹음입니다. 보관된 미전사 목록에서 먼저 전사 예정에 추가하세요.')
        if not asset["stable"]:
            raise ValueError("파일이 변경 중이거나 확인 대기 중입니다. 녹음·백업 종료 후 새로고침하세요.")
        supplied_title = selection.get("title") or ""
        if not isinstance(supplied_title, str):
            raise ValueError("강의 제목 힌트는 글자로 입력하거나 비워두세요.")
        title = supplied_title.strip() or Path(store.original_name(asset)).stem[:120]
        hints = str(selection.get("hints", "")).strip()
        if not title or len(title) > 120 or len(hints) > 1500:
            raise ValueError("제목은 1~120자, 용어 힌트는 1500자 이내로 입력하세요.")
        template = freeze(selection.get("template_id", "raw"), selection.get("prompt"))
        auto_title = selection.get("auto_title", False)
        if type(auto_title) is not bool:
            raise ValueError("자동 제목 설정을 확인하세요.")
        ai = dict(settings.get("note_ai", {"provider": "disabled"}))
        ai_identity = selection.get("note_ai_id")
        if ai_identity:
            choices = settings.get("note_ai_profiles", {})
            if ai_identity not in choices:
                raise ValueError("등록된 정리 AI를 선택하세요.")
            ai = dict(choices[ai_identity])
            if not ai.get("available", False):
                raise ValueError("선택한 정리 AI의 설치·등록을 먼저 완료해 주세요.")
        if (template["id"] != "raw" or auto_title) and ai.get("provider") == "disabled":
            raise ValueError("정리·자동 제목에 사용할 AI 방식을 먼저 설정해 주세요. 전사 원문만 저장은 계속 사용할 수 있습니다.")
        role = selection.get("lecture_role", "수강")
        if role not in ("수강", "강의"):
            raise ValueError("강의·수강 구분을 확인하세요.")
        rename_source = auto_title and settings.get("rename_selected_audio", False)
        if rename_source and not settings.get("sync_rename_ready", False):
            raise ValueError("NAS 이름 변경 전 FolderSync의 원본 변경 시에만 재동기화 설정을 확인해 주세요.")
        start = float(selection.get("start", 0) or 0)
        end = selection.get("end")
        end = float(end) if end not in (None, "") else None
        if not math.isfinite(start) or start < 0 or (end is not None and (not math.isfinite(end) or end <= start)):
            raise ValueError("구간의 시작·끝 시간을 확인하세요.")
        path = safe_audio(Path(settings["nas_root"]), asset["relative_path"])
        before = path.stat()
        if (before.st_size, before.st_mtime_ns) != (asset["size"], asset["mtime_ns"]):
            raise ValueError("목록을 본 뒤 파일이 바뀌었습니다. 새로고침 후 다시 선택하세요.")
        fingerprint = digest(path)
        after = path.stat()
        if (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
            raise ValueError("파일이 아직 변경 중입니다. 잠시 후 다시 선택하세요.")
        params = {"title": title, "hints": hints, "start": start, "end": end,
                  "profile": profile, "chunk_seconds": settings["chunk_seconds"],
                  "nas_root": settings["nas_root"], "wiki_root": settings["wiki_root"],
                  "runtime_python": settings["runtime_python"], "app_port": settings["port"], "contract": 4}
        params.update(note_template=template, auto_title=auto_title, note_ai=ai,
                      title_context=supplied_title.strip(), note_layout="paragraphs-v1",
                      filename_mode="plaud", preserve_modified=True, lecture_role=role,
                      original_filename=store.original_name(asset), rename_source=rename_source,
                      sync_rename_ready=settings.get("sync_rename_ready", False))
        key = hashlib.sha256((fingerprint + json.dumps(params, ensure_ascii=False, sort_keys=True)).encode()).hexdigest()
        prepared.append((key, asset, fingerprint, params))
    result = []
    with store.db() as db:
        db.execute("BEGIN IMMEDIATE")
        for key, asset, fingerprint, params in prepared:
            prior = db.execute("SELECT id FROM jobs WHERE dedupe_key=?", (key,)).fetchone()
            if prior:
                result.append({"id": prior["id"], "reused": True})
                continue
            identity = uuid.uuid4().hex
            now = time.time()
            db.execute("""INSERT INTO jobs
                (id,dedupe_key,asset_id,relative_path,source_sha,source_size,source_mtime_ns,
                 params,requested_at,status,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (identity, key, asset["id"], asset["relative_path"], fingerprint,
                 asset["size"], asset["mtime_ns"], json.dumps(params, ensure_ascii=False), now, "queued", now))
            result.append({"id": identity, "reused": False})
    return result


def retry(store, identity):
    job = store.job(identity)
    if not job or job["status"] not in ("failed", "cancelled", "waiting_ai"):
        raise ValueError("실패·취소한 선택 작업만 다시 시작할 수 있습니다.")
    if store.asset(job['asset_id']).get('excluded'):
        raise ValueError('전사 예정에서 뺀 녹음입니다. 먼저 전사 예정에 추가하세요.')
    store.change_job(identity, status="queued", error=None, cancel_requested=0)
    attempt = store.home / "work" / identity / "note-ai-attempt.json"
    if attempt.exists():
        (attempt.parent / "allow-ai-retry").write_text("explicit retry", encoding="utf-8")


def cancel(store, identity):
    job = store.job(identity)
    if not job or job["status"] in ("completed", "cancelled"):
        raise ValueError("취소할 수 있는 작업이 아닙니다.")
    store.change_job(identity, cancel_requested=1,
                     status="cancelled" if job["status"] in ("queued", "queued_summary", "failed", "waiting_ai") else job["status"])
