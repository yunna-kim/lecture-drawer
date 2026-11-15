from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from .naming import note_title, safe_title, title_context
from .paragraphs import group_segments
from .catalog import approved_source


def timestamp(seconds):
    seconds = int(seconds)
    return f"{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}"


def publish(store, job, segments, version, note=None):
    fresh = store.job(job["id"])
    if fresh["cancel_requested"]:
        raise ValueError("취소한 작업은 옵시디언에 저장하지 않습니다.")
    params = job["params"]
    # Summarization may take time after ASR. Recheck the selected source at the
    # final save boundary, including raw-only jobs that do not rename the audio.
    try:
        approved_source(fresh, {"nas_root": params["nas_root"]})
    except OSError as error:
        raise ValueError("저장 직전 NAS 원음을 확인하지 못했습니다. 전사 결과는 보존했습니다. NAS 연결·파일 위치를 확인한 뒤 같은 작업을 재시도하세요.") from error
    note = note or {}
    root = Path(params["wiki_root"])
    if not root.is_dir():
        raise ValueError("전사 결과 폴더가 없습니다. 설정의 WIKI 경로를 확인하세요.")
    if not segments or not any(s["text"].strip() for s in segments):
        raise ValueError("인식된 문장이 없습니다. 원음을 확인해 주세요. 빈 노트는 만들지 않았습니다.")
    stamp = datetime.fromtimestamp(job["requested_at"]).astimezone()
    title = re.sub(r'[<>:"/\\|?*\[\]#^\x00-\x1f]', "_", params["title"]).strip(" .")[:100] or "강의"
    if params.get("filename_mode") == "plaud":
        title, date_basis = note_title(job, note.get("title"))
        dest = root / (title + ".md")
    else:
        date_basis = "note-created-date"
        dest = root / f"{stamp:%Y%m%d-%H%M} {title} 전사 {job['id'][:8]}.md"
    body = "\n\n".join(f"### {timestamp(s['start'])}\n\n{s['text'].strip()}" for s in group_segments(segments)) + "\n"
    body_sha = hashlib.sha256(body.encode()).hexdigest()
    audio = Path(params["nas_root"]) / job.get("resolved_relative_path", job["relative_path"])
    meta = {
        "type": "raw-source", "sourceKind": "local-recording-transcript", "verified": False,
        "sourceId": hashlib.sha256(("local-transcript:" + job["id"]).encode()).hexdigest(), "source": str(audio),
        "sourceSha256": body_sha,
        "sourceBinarySha256": job["source_sha"], "contentSha256": body_sha,
        "date created": stamp.strftime("%Y-%m-%d"), "recordedAtDevice": None,
        "sourceFileModifiedAt": datetime.fromtimestamp(job["source_mtime_ns"] / 1e9).astimezone().isoformat(),
        "transcriptionProfile": params["profile"]["id"], "transcriptionVersions": version,
        "selectedAt": stamp.isoformat(), "category": "Transcripts", "status": "unreviewed",
        "tags": ["raw-source", "transcript", "Status/Reading"],
        "originalAudioFilename": params.get("original_filename", job["relative_path"]),
        "audioFilename": audio.name, "titleDateBasis": date_basis,
        "noteTemplate": params.get("note_template", {}).get("id", "raw"),
        "templateSha256": params.get("note_template", {}).get("sha256"),
        "noteAiProvider": note.get("provider", "none"),
        "noteAiPromptSha256": note.get("prompt_sha256"),
        "noteAiGeneration": note.get("generation", {}),
        "titleContext": note.get("title_context_override", title_context(params, job["relative_path"])),
        "titleContextAtRequest": title_context(params, job["relative_path"]),
        "titleProvenance": note.get("title_source", "ai" if params.get("auto_title") else "user"),
        "originalAiTitle": note.get("original_ai_title"),
        "transcriptLayout": "paragraphs-v1",
        "aiSummarySha256": hashlib.sha256(note.get("summary", "").encode()).hexdigest(),
    }
    header = "---\n" + "\n".join(k + ": " + json.dumps(v, ensure_ascii=False) for k, v in meta.items()) + "\n---\n"
    heading = title if params.get("filename_mode") == "plaud" else params["title"]
    prefix = f"\n# {heading}\n\n로컬 전사 원문 · 사람 검토 전\n\n"
    prefix += "원래 음성 파일명: " + json.dumps(params.get("original_filename", job["relative_path"]), ensure_ascii=False) + "\n\n"
    prefix += "현재 NAS 파일명: " + json.dumps(audio.name, ensure_ascii=False) + "\n\n"
    prefix += f"[원음·해당 구간 확인](http://127.0.0.1:{params.get('app_port',8767)}/?job={job['id']})\n\n"
    summary = note.get("summary", "")
    text = header + prefix + ("## AI 정리 · 검토 전\n\n" + summary + "\n\n" if summary else "") + "## Original Content\n" + body
    payload = text.encode("utf-8")
    expected = hashlib.sha256(payload).hexdigest()
    # Complete a local temp write first, then publish without replacing an existing note.
    fd, tmp = tempfile.mkstemp(prefix=".transcript-", suffix=".tmp", dir=root)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if params.get("preserve_modified"):
            os.utime(tmp, ns=(job["source_mtime_ns"], job["source_mtime_ns"]))
        try:
            if os.name == "nt":
                os.rename(tmp, dest)  # Windows rename fails if destination already exists.
            else:
                os.link(tmp, dest)
        except FileExistsError:
            if hashlib.sha256(dest.read_bytes()).hexdigest() != expected:
                raise ValueError("동일 작업의 기존 노트가 수정돼 있습니다. 기존 내용을 보존하고 저장을 멈췄습니다.")
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    if hashlib.sha256(dest.read_bytes()).hexdigest() != expected:
        raise ValueError("옵시디언 저장 후 내용 확인에 실패했습니다.")
    store.change_job(job["id"], status="completed", output_path=str(dest), output_sha=expected, error=None,
                     progress={"fraction": 1, "message": "전사 원문 저장 완료 · 내용을 검토해 주세요."})
    return dest
