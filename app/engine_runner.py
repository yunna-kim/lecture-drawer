"""Isolated local inference process. Only a persisted, selected job can run."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
from contextlib import nullcontext

from .catalog import approved_source, digest
from .config import atomic_json
from .profiles import checked_profile, runtime_versions, inference_compute_type
from .store import Store
from .segments import owned_segments
from .chunks import plan_chunks

DLL_HANDLES = []


def tools_env():
    # No network/model downloads during transcription.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
    try:
        import nvidia
        for base in getattr(nvidia, "__path__", []):
            for folder in Path(base).glob("*/bin"):
                if folder.is_dir():
                    DLL_HANDLES.append(os.add_dll_directory(str(folder)))
                    os.environ["PATH"] = str(folder) + os.pathsep + os.environ.get("PATH", "")
    except ImportError:
        pass


def run(job_id: str, home: Path):
    store = Store(home)
    job = store.job(job_id)
    if not job or job["status"] != "running" or job["cancel_requested"]:
        raise ValueError("실행할 수 있는 사용자 선택 작업이 없습니다.")
    params = job["params"]
    if params.get("contract") != 4:
        raise ValueError("이전 요청의 처리 방식이 바뀌었습니다. 목록에서 강의를 다시 선택해 새 작업을 만드세요.")
    path = approved_source(job, {"nas_root": params["nas_root"]})
    profile = checked_profile(params["profile"])
    work = home / "work" / job_id
    work.mkdir(parents=True, exist_ok=True)
    progress = lambda **value: atomic_json(work / "progress.json", value)
    progress(stage="loading", message="로컬 모델을 준비하고 있습니다.")
    tools_env()
    ffprobe = shutil.which("ffprobe")
    ffmpeg = shutil.which("ffmpeg")
    if not ffprobe or not ffmpeg:
        raise ValueError("FFmpeg가 필요합니다. 환경 점검을 실행하세요.")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    info = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration",
                           "-of", "json", str(path)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=90, creationflags=flags, check=True)
    duration = float(json.loads(info.stdout)["format"]["duration"])
    start = params["start"]
    end = min(params["end"] if params["end"] is not None else duration, duration)
    if not math.isfinite(duration) or duration <= 0 or start >= end:
        raise ValueError("음성 길이 또는 선택 구간을 확인해 주세요.")
    import ctranslate2
    from faster_whisper import WhisperModel
    device = profile["device"]
    if device == "auto":
        device = "cuda" if ctranslate2.get_cuda_device_count() else "cpu"
    compute = inference_compute_type(profile, device)
    from .gpu_schedule import voice_session
    if device=='cuda':progress(stage='gpu-handoff',message='음성 전사 우선 · 로컬 요약의 GPU 메모리를 반환하고 있습니다.')
    with voice_session() if device=='cuda' else nullcontext():
        model = WhisperModel(profile["model_dir"], device=device, compute_type=compute,
                             local_files_only=True, cpu_threads=4, num_workers=1)
        try:
            progress(stage="planning", message="문장 사이의 쉼을 찾아 전사 구간을 나누고 있습니다.")
            plan = plan_chunks(path, start, end, params["chunk_seconds"], ffmpeg, flags)
            chunks = len(plan)
            output = []
            for index, (left, right) in enumerate(plan):
                current = store.job(job_id)
                if current["cancel_requested"] or current["status"] != "running":
                    raise ValueError("사용자가 작업을 취소했습니다.")
                decode_left = max(start, left - 2)
                decode_right = min(end, right + 2)
                receipt = work / f"chunk-{index:05}.json"
                if receipt.exists():
                    saved = json.loads(receipt.read_text("utf-8"))
                    if saved["dedupe_key"] != job["dedupe_key"] or saved["start"] != left or saved["end"] != right:
                        raise ValueError("이전 구간 결과가 현재 요청과 다릅니다. 재사용을 중단했습니다.")
                    output.extend(saved["segments"])
                    continue
                progress(stage="transcribing", chunk=index + 1, chunks=chunks,
                         fraction=index / chunks, seconds=left,
                         message=f"{index + 1}/{chunks} 구간 전사 중 · {device.upper()}")
                # Five minutes per chunk bounds memory; the NAS original remains untouched.
                wav = work / "current-chunk.wav"
                subprocess.run([ffmpeg, "-v", "error", "-nostdin", "-y", "-ss", str(decode_left), "-i", str(path),
                                "-t", str(decode_right - decode_left), "-vn", "-ar", "16000", "-ac", "1", str(wav)],
                               capture_output=True, timeout=180, creationflags=flags, check=True)
                try:
                    segments, _ = model.transcribe(
                        str(wav), language=profile["language"], beam_size=5, vad_filter=True,
                        word_timestamps=True, initial_prompt=params["hints"] or None,
                        hotwords=params["hints"] or None,
                    )
                    rows = owned_segments(segments, decode_left, left, right)
                    atomic_json(receipt, {"dedupe_key": job["dedupe_key"], "start": left, "end": right, "segments": rows})
                    output.extend(rows)
                finally:
                    wav.unlink(missing_ok=True)
            # Read again after inference; never publish if the selected source changed.
            approved_source(job, {"nas_root": params["nas_root"]})
            atomic_json(work / "result.json", {"source_sha": job["source_sha"], "dedupe_key": job["dedupe_key"],
                                             "duration": duration, "segments": output, "chunks": plan,
                                             "versions": {**runtime_versions(), "device": device,
                                                          "compute_type":compute, "model": profile["model_fingerprint"]}})
            progress(stage="finished", fraction=1, message="전사 완료 · 저장 준비")

        finally:
            del model


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", required=True)
    parser.add_argument("--state", type=Path, required=True)
    args = parser.parse_args()
    try:
        run(args.job, args.state)
    except Exception as error:
        # Keep details local to the explicitly selected job, not a shared vault.
        atomic_json(args.state / "work" / args.job / "failure.json",
                    {"type": type(error).__name__, "message": str(error)[:1500]})
        sys.exit(1)
