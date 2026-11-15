"""Open an existing note in Obsidian without relying on .md file associations."""
from pathlib import Path
from urllib.parse import quote, urlencode


def note_uri(path: Path):
    path = path.resolve(strict=True)
    if not path.is_file() or path.suffix.lower() != ".md":
        raise ValueError("저장된 Markdown 노트만 열 수 있습니다.")
    return "obsidian://open?" + urlencode({"path": path.as_posix(), "paneType": "tab"}, quote_via=quote)
