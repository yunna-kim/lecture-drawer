"""Explicit user correction using saved ASR/summary; never runs a model."""
import json
import os
from pathlib import Path

from .catalog import approved_source, digest
from .config import atomic_json
from .naming import note_title, safe_title
from .output import publish


def revise_completed(store, identity, topic):
    job=store.job(identity)
    if not job or job['status']!='completed' or job['cancel_requested']:
        raise ValueError('완료한 선택 작업만 수정할 수 있습니다.')
    if not isinstance(topic,str) or not topic.strip() or len(topic)>100:
        raise ValueError('수정할 제목을 1~100자로 입력하세요.')
    root=Path(job['params']['wiki_root']).resolve(strict=True)
    old_note=Path(job['output_path']).resolve(strict=True)
    if old_note.suffix.lower()!='.md' or not old_note.is_relative_to(root):
        raise ValueError('등록된 결과 노트가 지정 폴더 밖에 있습니다.')
    if digest(old_note)!=job['output_sha']:
        raise ValueError('저장 후 사용자가 편집한 노트입니다. 기존 편집을 보존하기 위해 자동 재작성을 멈췄습니다.')
    source=approved_source(job,{'nas_root':job['params']['nas_root']})
    source_stat=source.stat()
    new_note=root/(note_title(job,topic)[0]+'.md')
    if new_note!=old_note and new_note.exists():
        raise ValueError('같은 제목의 노트가 이미 있어 덮어쓰지 않았습니다.')
    original=job['params'].get('original_filename',job['relative_path'])
    new_audio=source.parent/(Path(original).stem+'_'+safe_title(topic,100)+Path(original).suffix) if job['params'].get('rename_source') else source
    if len(new_audio.name)>240:
        raise ValueError('원래 파일명을 보존하면 이름이 너무 깁니다. 제목을 짧게 지정해 주세요.')
    if new_audio!=source and new_audio.exists():
        raise ValueError('같은 이름의 음성이 이미 있어 덮어쓰지 않았습니다.')
    work=store.home/'work'/identity
    result=json.loads((work/'result.json').read_text('utf-8'))
    note=json.loads((work/'note-result.json').read_text('utf-8'))
    if result['dedupe_key']!=job['dedupe_key'] or result['source_sha']!=job['source_sha']:
        raise ValueError('보관된 전사 결과가 선택 기록과 다릅니다.')
    revised={**note,'title':topic.strip(),'original_ai_title':note.get('original_ai_title') or note.get('title'),
             'title_source':'user-correction','title_context_override':topic.strip()}
    staged=root/('.revise-'+identity+'.tmp')
    if staged.exists():
        raise ValueError('이전 수정의 임시 노트가 남았습니다. 복구 상태를 먼저 확인하세요.')
    journal={'status':'prepared','old_note':str(old_note),'new_note':str(new_note),
             'old_audio':source.name,'new_audio':new_audio.name,'source_sha':job['source_sha'],
             'source_mtime_ns':job['source_mtime_ns'],'previous_note_sha':job['output_sha'],
             'previous_note':old_note.read_text('utf-8'),'previous_note_result':note}
    atomic_json(work/'revision.json',journal)
    try:
        if new_audio!=source:
            os.rename(source,new_audio)
            if new_audio.stat().st_mtime_ns!=source_stat.st_mtime_ns:
                os.utime(new_audio,ns=(source_stat.st_atime_ns,source_stat.st_mtime_ns))
            if digest(new_audio)!=job['source_sha'] or new_audio.stat().st_mtime_ns!=job['source_mtime_ns']:
                raise ValueError('음성 내용·수정 시각 검증에 실패했습니다. 파일을 보존하고 중단합니다.')
            with store.db() as db:
                row=db.execute('SELECT id FROM renames WHERE job_id=?',(identity,)).fetchone()
                if row:
                    db.execute("UPDATE renames SET previous_path=?,renamed_path=?,status='completed' WHERE job_id=?",
                               (source.name,new_audio.name,identity))
                else:
                    db.execute('INSERT INTO renames(job_id,asset_id,source_sha,original_path,previous_path,renamed_path,mtime_ns,size,status) VALUES(?,?,?,?,?,?,?,?,?)',
                               (identity,job['asset_id'],job['source_sha'],original,source.name,new_audio.name,
                                job['source_mtime_ns'],job['source_size'],'completed'))
        if digest(old_note)!=job['output_sha']:
            raise ValueError('수정 중 노트가 바뀌었습니다. 사용자 편집은 보존했습니다.')
        os.rename(old_note,staged)
        if digest(staged)!=job['output_sha']:
            raise ValueError('노트가 수정 중입니다. 임시 노트를 보존했습니다.')
        path=publish(store,store.job(identity),result['segments'],result['versions'],note=revised)
        if digest(staged)!=job['output_sha']:
            raise ValueError('수정 중 사용자 편집이 발생해 임시 노트를 보존했습니다.')
        staged.unlink()
        atomic_json(work/'note-result.json',revised)
        atomic_json(work/'revision.json',{**journal,'status':'completed','new_note_sha':digest(path)})
        return path
    except Exception as error:
        if staged.exists() and not old_note.exists():
            os.rename(staged,old_note)
        atomic_json(work/'revision.json',{**journal,'status':'review','error':str(error)[:500]})
        raise
