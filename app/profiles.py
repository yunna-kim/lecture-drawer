from __future__ import annotations

import hashlib
import importlib.metadata
import json
from pathlib import Path
import time

from .catalog import digest
from .config import load_settings, save_settings


def register_whisper(home: Path, model_dir: Path, label: str, device="auto", compute_type=None):
    if compute_type not in (None,"float16","int8_float16","int8"):
        raise ValueError("지원하는 전사 계산 방식은 float16/int8_float16/int8입니다.")
    if device=="cpu" and compute_type in ("float16","int8_float16"):
        raise ValueError("CPU 모델은 int8을 선택하세요.")
    model_dir = model_dir.resolve(strict=True)
    required = ["model.bin", "config.json", "tokenizer.json", "vocabulary.json"]
    for name in required:
        if not (model_dir / name).is_file():
            raise ValueError("Whisper 모델 파일이 부족합니다: " + name)
    files = {name: digest(model_dir / name) for name in required}
    fingerprint = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    identity = "whisper-" + fingerprint[:12] + "-" + device
    if compute_type is not None:identity+="-"+compute_type
    settings = load_settings(home)
    settings["profiles"][identity] = {
        "id": identity, "engine": "faster-whisper", "label": label,
        "model_dir": str(model_dir), "model_fingerprint": fingerprint,
        "files": files, "device": device, "language": "ko",
        "runtime_versions": runtime_versions(),
        "compute_type": compute_type or ("float16" if device != "cpu" else "int8"),
        "registered_at": time.time(), "tested": False,
    }
    # First profile is a starting baseline, not a claim of measured superiority.
    if not settings["active_profile"]:
        settings["active_profile"] = identity
    save_settings(home, settings)
    return identity


def checked_profile(profile):
    if not profile or profile.get("engine") != "faster-whisper":
        raise ValueError("아직 준비된 전사 엔진이 없습니다. 모델 등록을 먼저 진행하세요.")
    if profile.get("runtime_versions") != runtime_versions():
        raise ValueError("전사 라이브러리 버전이 바뀌었습니다. 환경 검증 후 모델을 새로 등록하세요.")
    root = Path(profile["model_dir"])
    for name, expected in profile["files"].items():
        if digest(root / name) != expected:
            raise ValueError("등록된 모델 파일이 변경됐습니다. 검증 후 새 버전으로 등록하세요.")
    return profile


def runtime_versions():
    value = {}
    for package in ("faster-whisper", "ctranslate2", "av"):
        try:
            value[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            value[package] = "not-installed"
    return value


def activate(home, store, profile_id, reviewed_job=None, rollback=False):
    if store.busy():
        raise ValueError("진행·대기 작업이 있습니다. 작업이 끝난 뒤 모델을 바꾸세요.")
    settings = load_settings(home)
    target = settings.get("previous_profile") if rollback else profile_id
    if target not in settings["profiles"]:
        raise ValueError("등록된 이전 모델이 없습니다.")
    if not rollback:
        job = store.job(reviewed_job)
        if not job or job["status"] != "completed" or job["params"]["profile"]["id"] != target:
            raise ValueError("이 모델로 완료한 시험 작업을 검토한 뒤 적용하세요.")
    checked_profile(settings["profiles"][target])
    previous = settings["active_profile"]
    settings["active_profile"] = target
    settings["previous_profile"] = previous
    settings["profiles"][target]["tested"] = True
    save_settings(home, settings)
    return target


def inference_compute_type(profile, device):
    value=profile.get('compute_type') or ('float16' if device=='cuda' else 'int8')
    if value not in ('float16','int8_float16','int8'):
        raise ValueError('등록된 모델의 계산 방식이 지원되지 않습니다.')
    return 'int8' if device=='cpu' else value
