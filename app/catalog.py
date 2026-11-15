from __future__ import annotations

import hashlib
import os
from pathlib import Path
import time

AUDIO_EXTENSIONS = {".m4a", ".mp3", ".wav", ".ogg", ".opus", ".oga", ".aac", ".flac", ".webm", ".mp4"}


def digest(path: Path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def safe_audio(root: Path, relative: str) -> Path:
    rel = Path(relative)
    if rel.is_absolute() or ".." in rel.parts or ":" in relative:
        raise ValueError("허용되지 않은 파일 경로입니다.")
    boundary = root.resolve(strict=True)
    path = (boundary / rel).resolve(strict=True)
    if not path.is_relative_to(boundary) or path.suffix.lower() not in AUDIO_EXTENSIONS or not path.is_file():
        raise ValueError("지정한 NAS 폴더의 음성 파일만 선택할 수 있습니다.")
    return path


def scan(store, settings):
    root = Path(settings["nas_root"])
    now = time.time()
    try:
        if not root.is_dir():
            raise OSError("NAS 폴더에 연결할 수 없습니다.")
        found = []
        skipped = 0
        logical_bytes = 0
        # Only this explicitly chosen folder, not its parent or other shares.
        entries = list(os.scandir(root))
        names = {entry.name for entry in entries}
        aliases = {name: identity for name, identity in store.rename_aliases().items() if name in names}
        for entry in entries:
            if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                skipped += 1
                continue
            if Path(entry.name).suffix.lower() not in AUDIO_EXTENSIONS:
                continue
            if entry.name.startswith(".") or any(x in entry.name.lower() for x in (".tmp.", ".part.", ".partial.")):
                skipped += 1
                continue
            stat = entry.stat(follow_symlinks=False)
            identity = aliases.get(entry.name) or hashlib.sha256(entry.name.encode("utf-8")).hexdigest()[:24]
            if entry.name not in aliases and identity in aliases.values():
                identity = hashlib.sha256(("reappeared:" + entry.name).encode("utf-8")).hexdigest()[:24]
            found.append((identity, entry.name, stat))
            logical_bytes += stat.st_size
        with store.db() as db:
            db.execute("UPDATE assets SET present=0")
            for identity, name, stat in found:
                old = db.execute("SELECT * FROM assets WHERE id=?", (identity,)).fetchone()
                same = old and old["size"] == stat.st_size and old["mtime_ns"] == stat.st_mtime_ns
                observed = old["observed_at"] if same else now
                stable = int(stat.st_size > 0 and now - observed >= settings["stable_seconds"])
                db.execute("INSERT OR REPLACE INTO assets VALUES(?,?,?,?,?,?,1)",
                           (identity, name, stat.st_size, stat.st_mtime_ns, observed, stable))
        result = {"connected": True, "last_scan": now, "files": len(found),
                  "logical_bytes": logical_bytes, "skipped": skipped,
                  "note": "녹음 폴더 목록 확인. 휴대폰 원본과 바이트 일치 여부는 동기화 도구에서 별도 확인하세요."}
    except OSError:
        # Do not erase a catalogue or pretend an inaccessible share is empty.
        result = {"connected": False, "last_scan": now,
                  "error": "녹음 폴더에 접근할 수 없습니다. 폴더 주소·권한과 NAS를 쓴다면 네트워크·전원을 확인하세요. 기존 작업은 보존됩니다."}
    store.meta("catalog", result)
    return result


def approved_source(job, settings):
    if not job or not job.get("requested_at") or not job.get("source_sha"):
        raise ValueError("사용자가 선택한 전사 요청이 없습니다.")
    path = safe_audio(Path(settings["nas_root"]), job.get("resolved_relative_path", job["relative_path"]))
    before = path.stat()
    if before.st_size != job["source_size"] or before.st_mtime_ns != job["source_mtime_ns"]:
        raise ValueError("선택한 뒤 파일이 바뀌었습니다. 새 파일을 확인하고 다시 선택하세요.")
    if digest(path) != job["source_sha"]:
        raise ValueError("선택한 원음과 지문이 다릅니다. 전사를 중단했습니다.")
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("파일이 아직 변경 중입니다. 녹음·백업 완료 후 다시 선택하세요.")
    return path
