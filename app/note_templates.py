"""Read the existing PLAUD prompt references without copying or editing them."""
import hashlib
from pathlib import Path
import re

DEFAULT_ROOT = Path(__file__).resolve().parent.parent / "note-templates"
LABELS = {"note-lecture": "강의·세미나", "note-meeting": "회의", "note-general": "일반 노트",
          "note-knowledge": "지식 정리", "note-coaching": "코칭", "note-interview": "인터뷰",
          "note-product": "제품·기획", "note-personal": "개인 메모", "note-consulting": "상담·컨설팅",
          "note-finance": "재무", "note-spiritual": "설교·묵상"}


def read_template(identity, root=DEFAULT_ROOT):
    if not isinstance(identity, str) or not re.fullmatch(r"[a-z0-9-]{1,60}", identity):
        raise ValueError("올바른 템플릿을 선택하세요.")
    path = root / (identity + ".md")
    if path.is_symlink() or not path.is_file():
        raise ValueError("템플릿을 찾을 수 없습니다.")
    raw = path.read_bytes()
    text = raw.decode("utf-8-sig")
    match = re.match(r"\A---\r?\n(.*?)\r?\n---\r?\n(.*)\Z", text, re.S)
    if not match:
        raise ValueError("템플릿 형식이 올바르지 않습니다.")
    front, body = match.groups()
    body = body.replace("\r\n", "\n")
    meta = dict(re.findall(r"(?m)^([a-z_]+):\s*([^\r\n]*)", front))
    if meta.get("kind") != "summary" or meta.get("deprecated", "false").lower() == "true":
        raise ValueError("정리용 현행 템플릿만 선택할 수 있습니다.")
    return {"id": identity, "label": LABELS.get(identity, identity), "body": body.strip(),
            "source_path": str(path), "source_sha": hashlib.sha256(raw).hexdigest(),
            "description": meta.get("description", "")}


def catalog(root=DEFAULT_ROOT):
    rows = [{"id": "raw", "label": "전사 원문만 · 요약 없음", "description": "시간표시가 있는 전사 원문을 보존합니다. 자동 제목도 끄면 정리 AI를 사용하지 않습니다."}]
    if root.is_dir():
        for path in sorted(root.glob("*.md")):
            try:
                item = read_template(path.stem, root)
                rows.append({k: item[k] for k in ("id", "label", "description")})
            except ValueError:
                pass
    return rows


def freeze(identity="raw", custom=None, root=DEFAULT_ROOT):
    if identity == "raw":
        return {"id": "raw", "label": "전사 원문만", "body": "", "sha256": hashlib.sha256(b"").hexdigest()}
    item = read_template(identity, root)
    body = custom if custom is not None else item["body"]
    if not isinstance(body, str) or not 1 <= len(body.strip()) <= 20000:
        raise ValueError("정리 프롬프트는 1~20,000자여야 합니다.")
    item["body"] = body.strip()
    item["sha256"] = hashlib.sha256(item["body"].encode()).hexdigest()
    return item


def fill_prompt(template, *, title, hints, transcript):
    values = {"title": title, "keywords": hints or "언급 없음", "speakers": "미확인", "transcript": transcript}
    # One substitution pass: placeholder-like source text must stay verbatim.
    rendered = re.sub(r"\{(title|keywords|speakers|transcript)\}", lambda m: values[m[1]], template["body"])
    if "{transcript}" not in template["body"]:
        rendered += "\n\n전사 원문:\n" + transcript
    return rendered
