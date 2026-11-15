"""Prefer pauses near each memory-bounded cut; never inspect unselected audio."""
import re
import subprocess


def pause_cut(log, offset, earliest, target):
    pending = None
    candidates = []
    for kind, value in re.findall(r"silence_(start|end):\s*([0-9.]+)", log):
        time = offset + float(value)
        if kind == "start":
            pending = time
        elif pending is not None:
            midpoint = (pending + time) / 2
            if earliest <= midpoint <= target:
                candidates.append(midpoint)
            pending = None
    return max(candidates) if candidates else target


def plan_chunks(path, start, end, seconds, ffmpeg, flags=0):
    if seconds < 4:
        raise ValueError("전사 구간은 최소 4초여야 합니다.")
    chunks = []
    left = start
    while left < end:
        target = min(end, left + seconds)
        right = target
        if target < end:
            earliest = max(left + seconds / 2, target - 8)
            probe = subprocess.run(
                [ffmpeg, "-hide_banner", "-nostdin", "-ss", str(earliest), "-i", str(path),
                 "-t", str(min(end, target + 1) - earliest), "-vn", "-af",
                 "silencedetect=noise=-35dB:d=0.25", "-f", "null", "-"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=90, creationflags=flags, check=True)
            right = pause_cut(probe.stderr, earliest, earliest, target)
        chunks.append((left, right))
        left = right
    return chunks
