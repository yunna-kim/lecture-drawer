"""Reversible presentation view; never remove source files or processing history."""
from copy import deepcopy
from pathlib import Path


def selection(store):
    value=store.meta('presentation') or {}
    return value if value.get('enabled') is True else {}


def filtered_status(store, value):
    config=selection(store)
    if not config:return {**value,'presentation':False}
    value=deepcopy(value)
    allowed=set(config.get('visible_job_ids',[]))
    jobs=[job for job in value['jobs'] if job['id'] in allowed]
    assets={job['asset_id'] for job in jobs} | set(config.get('visible_asset_ids',[]))
    titles=config.get('fixed_titles',{})
    aliases=config.get('display_aliases',{})
    names={asset['id']:Path(asset['relative_path']).name for asset in value['assets'] if asset['id'] in assets}
    for job in jobs:
        alias=aliases.get(job['id'])
        title=alias or titles.get(job['id'],job['params']['title'])
        name=names.get(job['asset_id'],Path(job['relative_path']).name)
        if alias:
            name=alias+Path(name).suffix
            names[job['asset_id']]=name
        job['display_title']=Path(name).stem
        job['params']={'title':title,'profile':{k:job['params']['profile'].get(k) for k in ('id','label')}}
        job['relative_path']=name
        if job.get('output_path'):job['output_path']=alias+'.md' if alias else Path(job['output_path']).name
        if alias and job.get('status')=='completed':job['progress']={'message':'전사 저장 완료'}
    value['jobs']=jobs
    value['assets']=[a for a in value['assets'] if a['id'] in assets]
    for asset in value['assets']:asset['relative_path']=names[asset['id']]
    value['settings']['nas_root']='시연용 선택 녹음 · 실제 경로 숨김'
    value['settings']['wiki_root']='Obsidian · 실제 경로 숨김'
    value['catalog']={**value['catalog'],'files':len(value['assets'])}
    value['presentation']=True
    value['api_credit']=None
    return value


def fixed_title_job(store, job):
    # Explicit per-job user correction, separate from the original frozen request.
    title=(store.meta('fixed_note_titles') or {}).get(job['id'])
    if not title:return job,None
    if not isinstance(title,str) or not 1<=len(title.strip())<=80:
        raise ValueError('고정 제목을 확인하세요.')
    return {**job,'params':{**job['params'],'title':title,'title_context':title,'auto_title':False}},title
