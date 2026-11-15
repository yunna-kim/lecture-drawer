from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import sys

ROOT = Path(__file__).resolve().parent.parent


def atomic_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    if path.exists() and path.read_text("utf-8") == payload:
        return
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def state_home() -> Path:
    return Path(os.environ.get("LECTURE_DRAWER_STATE", "") or
                (Path(os.environ.get("LOCALAPPDATA", Path.home())) / "LectureDrawer"))


def load_settings(home: Path | None = None):
    home = home or state_home()
    home.mkdir(parents=True, exist_ok=True)
    path = home / "settings.json"
    if not path.exists():
        # Fresh installations get empty local folders, never the author's NAS,
        # vault, credentials or machine-specific Python. Existing settings stay.
        source, output = home / 'input', home / 'output'
        source.mkdir(exist_ok=True); output.mkdir(exist_ok=True)
        atomic_json(path, {
            "schema": 1, "mode":"personal", "source_type":"local-folder",
            "nas_root": str(source), "wiki_root": str(output),
            "runtime_python": sys.executable,
            "port": 8767, "scan_seconds": 30, "stable_seconds": 15,
            "chunk_seconds": 300, "active_profile": None,
            "previous_profile": None, "profiles": {},
        })
    value = json.loads(path.read_text("utf-8"))
    if value.get("schema") != 1:
        raise ValueError("설정 형식이 지원되지 않습니다. 이전 버전 설정으로 복구하세요.")
    value["state_home"] = str(home)
    return value


def save_settings(home: Path, value):
    clean = {k: v for k, v in value.items() if k != "state_home"}
    atomic_json(home / "settings.json", clean)
