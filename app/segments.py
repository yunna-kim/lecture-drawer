def owned_segments(segments, decode_left, left, right):
    """Partition overlapping decoding windows by word time, never by matching text."""
    rows = []
    for segment in segments:
        words = getattr(segment, "words", None)
        if words:
            owned = [word for word in words
                     if left <= decode_left + (word.start + word.end) / 2 < right]
            if not owned:
                continue
            text = "".join(word.word for word in owned).strip()
            start, end = decode_left + owned[0].start, decode_left + owned[-1].end
        else:
            start, end = decode_left + segment.start, decode_left + segment.end
            if not left <= (start + end) / 2 < right:
                continue
            text = segment.text.strip()
        if text:
            rows.append({"start": round(start, 3), "end": round(end, 3), "text": text})
    return rows
