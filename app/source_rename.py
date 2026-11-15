"""Journaled same-folder rename of explicitly selected audio; never overwrite."""
import os
from pathlib import Path
from .catalog import approved_source, digest, safe_audio
from .naming import safe_title


def rename_selected(store, identity, topic):
    job = store.job(identity)
    if not job or job["cancel_requested"]:
        raise ValueError("취소했거나 선택하지 않은 원음은 이름을 바꾸지 않습니다.")
    params = job["params"]
    if not params.get("rename_source"):
        return job
    if not params.get("sync_rename_ready"):
        raise ValueError("FolderSync의 원본 변경 시에만 재동기화 설정을 먼저 확인하세요.")
    original = params.get("original_filename", job["relative_path"])
    suffix = safe_title(topic, 100)
    if not suffix:
        raise ValueError("강의 제목을 확인하지 못해 원음 이름을 바꾸지 않았습니다.")
    new_name = Path(original).stem + "_" + suffix + Path(original).suffix
    if len(new_name) > 240:
        raise ValueError("원래 파일명을 보존하면 이름이 너무 깁니다. 강의 제목을 짧게 지정해 주세요.")
    with store.db() as db:
        receipt = db.execute("SELECT * FROM renames WHERE job_id=?", (identity,)).fetchone()
    if receipt:
        if receipt["renamed_path"] != new_name:
            raise ValueError("이전 이름 변경 요청과 제목이 다릅니다. 작업을 확인하세요.")
        new = safe_audio(Path(params["nas_root"]), new_name) if (Path(params["nas_root"]) / new_name).is_file() else None
        old = Path(params["nas_root"]) / receipt["previous_path"]
        if new and not old.exists():
            if digest(new) != job["source_sha"] or new.stat().st_mtime_ns != job["source_mtime_ns"]:
                raise ValueError("이름 변경 후 원음·수정 시각이 달라졌습니다. 파일을 보존하고 중단했습니다.")
            with store.db() as db:
                db.execute("UPDATE renames SET status='completed' WHERE job_id=?", (identity,))
            return store.job(identity)
    source = approved_source(job, {"nas_root": params["nas_root"]})
    destination = source.parent / new_name
    if source.name == new_name:
        return job
    if destination.exists():
        raise ValueError("같은 이름의 NAS 파일이 이미 있습니다. 기존 파일을 보존하고 중단했습니다.")
    stat = source.stat()
    with store.db() as db:
        db.execute("INSERT OR IGNORE INTO renames(job_id,asset_id,source_sha,original_path,previous_path,renamed_path,mtime_ns,size,status) VALUES(?,?,?,?,?,?,?,?,?)",
                   (identity, job["asset_id"], job["source_sha"], original, source.name, new_name, stat.st_mtime_ns, stat.st_size, "prepared"))
    # On Windows rename refuses an existing destination. This application is Windows-only.
    if os.name != "nt":
        raise ValueError("NAS 이름 변경은 Windows에서만 지원합니다.")
    os.rename(source, destination)
    if destination.stat().st_mtime_ns != stat.st_mtime_ns:
        os.utime(destination, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    if destination.stat().st_mtime_ns != stat.st_mtime_ns or digest(destination) != job["source_sha"]:
        raise ValueError("수정 시각·원음 지문 검증에 실패했습니다. 원음을 삭제하지 않고 중단했습니다.")
    with store.db() as db:
        db.execute("UPDATE renames SET status='completed' WHERE job_id=?", (identity,))
    return store.job(identity)
