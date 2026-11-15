"""Explicit offline ASR comparison. Never publishes notes or invokes a summary AI."""
from contextlib import nullcontext
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import unicodedata

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.config import atomic_json


def normalized(text):
    return ''.join(c for c in unicodedata.normalize('NFKC',text).casefold()
                   if c.isalnum())


def character_errors(reference,hypothesis):
    reference,hypothesis=normalized(reference),normalized(hypothesis)
    if not reference:raise ValueError('비교 정답 문장이 필요합니다.')
    row=list(range(len(hypothesis)+1))
    for i,left in enumerate(reference,1):
        next_row=[i]
        for j,right in enumerate(hypothesis,1):
            next_row.append(min(row[j]+1,next_row[-1]+1,row[j-1]+(left!=right)))
        row=next_row
    return {'errors':row[-1],'characters':len(reference),'cer':row[-1]/len(reference)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--model',action='append',required=True,help='label=local_model_directory')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--device',choices=['cpu','cuda'],default='cpu')
    parser.add_argument('--compute-type',choices=['int8','int8_float16','float16'],default='int8')
    args=parser.parse_args()
    rows=json.loads(args.manifest.read_text('utf-8-sig'))
    if not isinstance(rows,list) or not 1<=len(rows)<=20:raise ValueError('시험 입력은1~20개만 선택하세요.')
    os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1')
    from app.engine_runner import tools_env
    tools_env()
    from faster_whisper import WhisperModel
    report={'kind':'synthetic-offline-asr-comparison','device':args.device,'compute_type':args.compute_type,
            'normalization':'NFKC/casefold/alphanumeric; no number rewriting',
            'reference_sha256':hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
            'inference':{'language':'ko','beam_size':5,'vad_filter':True,'word_timestamps':True},'models':[]}
    if args.output.exists():
        previous=json.loads(args.output.read_text('utf-8'))
        if any(previous.get(k)!=v for k,v in report.items() if k!='models'):
            raise ValueError('이전 비교 보고와 입력·추론 조건이 다릅니다. 다른 보고 위치를 사용하세요.')
        for old in previous.get('models',[]):
            for row,sample in zip(old['samples'],rows):
                audio=(args.manifest.parent/sample['audio']).resolve(strict=True)
                if row['audio_sha256']!=hashlib.sha256(audio.read_bytes()).hexdigest():
                    raise ValueError('시험 음성이 바뀌었습니다. 이전 결과를 재사용하지 않습니다.')
        report['models']=previous.get('models',[])
    from app.gpu_schedule import voice_session
    with voice_session() if args.device=='cuda' else nullcontext():
        for model_spec in args.model:
            label,folder=model_spec.split('=',1);folder=Path(folder).resolve(strict=True)
            model_sha=hashlib.sha256()
            with (folder/'model.bin').open('rb') as stream:
                for block in iter(lambda:stream.read(1024*1024),b''):model_sha.update(block)
            if any(old['model_sha256']==model_sha.hexdigest() for old in report['models']):
                print(label,'verified cached comparison',flush=True);continue
            started=time.monotonic()
            model=WhisperModel(str(folder),device=args.device,compute_type=args.compute_type,cpu_threads=4,num_workers=1,local_files_only=True)
            result={'label':label,'model_sha256':model_sha.hexdigest(),'load_seconds':round(time.monotonic()-started,3),'samples':[]}
            for sample in rows:
                audio=(args.manifest.parent/sample['audio']).resolve(strict=True)
                before=hashlib.sha256(audio.read_bytes()).hexdigest();started=time.monotonic()
                segments,info=model.transcribe(str(audio),language='ko',beam_size=5,vad_filter=True,word_timestamps=True)
                text=''.join(s.text for s in segments).strip()
                row={'id':sample['id'],'text':text,'reference':sample['reference'],
                     'audio_sha256':before,'audio_unchanged':before==hashlib.sha256(audio.read_bytes()).hexdigest(),
                     'duration':info.duration,'seconds':round(time.monotonic()-started,3),
                     **character_errors(sample['reference'],text)}
                result['samples'].append(row)
                print(label,sample['id'],'CER',round(row['cer'],4),'seconds',row['seconds'],flush=True)
            result['errors']=sum(row['errors'] for row in result['samples'])
            result['characters']=sum(row['characters'] for row in result['samples'])
            result['cer']=result['errors']/result['characters']
            report['models'].append(result);atomic_json(args.output,report)
            del model
    print('REPORT',args.output,flush=True)


if __name__=='__main__':main()
