"""Presentation-only grouping; detailed ASR timing stays in the job result."""
import re


def group_segments(segments):
    paragraphs=[]
    current=[]
    length=0
    def flush():
        nonlocal current,length
        if current:
            paragraphs.append({'start':current[0]['start'],'end':current[-1]['end'],
                               'text':' '.join(s['text'].strip() for s in current)})
        current=[];length=0
    for segment in segments:
        if not segment['text'].strip():
            continue
        if current:
            last=current[-1]
            elapsed=last['end']-current[0]['start']
            pause=segment['start']-last['end']
            sentence_end=bool(re.search(r'[.!?。？！]["”\')]*$',last['text'].strip()))
            if ((pause>=2 and length>=100) or (sentence_end and (elapsed>=45 or length>=450)) or length>=700):
                flush()
        current.append(segment);length+=len(segment['text'].strip())
    flush()
    return paragraphs
