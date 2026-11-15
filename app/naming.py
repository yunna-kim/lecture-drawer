from datetime import datetime
from pathlib import Path
import re
import unicodedata


def title_context(params, relative_path=""):
    if "title_context" in params:
        return params["title_context"].strip()
    title=params.get("title", "").strip()
    original=params.get("original_filename", relative_path)
    return "" if title in (Path(original).stem[:120],Path(relative_path).stem[:120]) else title


def missing_context_terms(context, title):
    normalize=lambda s:re.sub(r'[^\w]', '', unicodedata.normalize('NFKC',s).casefold())
    target=normalize(title)
    terms=re.findall(r'[\w]+',unicodedata.normalize('NFKC',context),re.UNICODE)
    missing=[]
    for term in terms:
        token=normalize(term)
        pattern=(r'(?<!\d)' if token[:1].isdigit() else '')+re.escape(token)
        if token and not re.search(pattern,target):missing.append(term)
    return missing


def safe_title(value, limit=100):
    value = re.sub(r'[<>:"/\\|?*\[\]#^\x00-\x1f]', "_", str(value)).strip(" ._")
    return value[:limit].strip(" ._")


def recording_time(filename):
    stem = Path(filename).stem
    patterns = (r"^(\d{8})[-_ ](\d{6}|\d{4})(?:\D|$)",
                r"^(\d{4})-(\d{4})-(\d{6})(?:\D|$)")
    for pattern in patterns:
        found = re.match(pattern, stem)
        if found:
            groups = found.groups()
            date, clock = (groups[0], groups[1]) if len(groups) == 2 else (groups[0] + groups[1], groups[2])
            try:
                return datetime.strptime(date + clock, "%Y%m%d%H%M%S" if len(clock) == 6 else "%Y%m%d%H%M")
            except ValueError:
                pass
    return None


def note_title(job, topic):
    params = job["params"]
    original = params.get("original_filename", job["relative_path"])
    recorded = recording_time(original)
    created = datetime.fromtimestamp(job["requested_at"]).astimezone()
    category = params.get("lecture_role", "수강")
    if category not in ("수강", "강의"):
        raise ValueError("강의·수강 구분을 확인하세요.")
    prefix = recorded.strftime("%Y%m%d-%H%M") if recorded else created.strftime("%Y%m%d")
    topic = safe_title(topic or params["title"])
    return f"{prefix} [{category}] {topic}", "filename" if recorded else "note-created-date"
