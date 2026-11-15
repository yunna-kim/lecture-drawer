import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from app.catalog import scan, approved_source, digest
from app.config import load_settings, save_settings
from app.naming import recording_time
from app.note_ai import compose
from app.note_templates import freeze
from app.output import publish
from app.requests import enqueue
from app.source_rename import rename_selected
from app.store import Store
from app.config import atomic_json
from app.revise import revise_completed


class NoteWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.home=base/'state';self.nas=base/'nas';self.wiki=base/'wiki'
        self.nas.mkdir();self.wiki.mkdir()
        self.path=self.nas/'20261031-164103_테스트녹음 1.wav'
        self.path.write_bytes(b'only-synthetic-test')
        self.mtime=self.path.stat().st_mtime_ns
        self.sha=digest(self.path)
        self.cfg=load_settings(self.home)
        profile={'id':'fixture','label':'fixture','engine':'faster-whisper','device':'cpu','tested':False}
        self.cfg.update(nas_root=str(self.nas),wiki_root=str(self.wiki),stable_seconds=0,
                        active_profile='fixture',profiles={'fixture':profile},
                        note_ai={'provider':'claude-subscription','approval':'fixture only'},
                        rename_selected_audio=True,sync_rename_ready=True)
        save_settings(self.home,self.cfg)
        self.store=Store(self.home);scan(self.store,self.cfg)
        self.asset=self.store.assets()[0]

    def tearDown(self):
        self.tmp.cleanup()

    def select(self, **options):
        identity=enqueue(self.store,self.cfg,[{'asset_id':self.asset['id'],**options}])[0]['id']
        return self.store.job(identity)

    def test_nas_rename_keeps_bytes_mtime_and_identity(self):
        job=self.select(auto_title=True)
        renamed=rename_selected(self.store,job['id'],'예시스터디 1교시')
        path=approved_source(renamed,self.cfg)
        self.assertEqual(path.name,'20261031-164103_테스트녹음 1_예시스터디 1교시.wav')
        self.assertEqual(path.stat().st_mtime_ns,self.mtime)
        self.assertEqual(digest(path),self.sha)
        self.assertFalse(self.path.exists())
        scan(self.store,self.cfg)
        self.assertEqual(self.store.assets()[0]['id'],self.asset['id'])
        self.assertEqual(rename_selected(self.store,job['id'],'예시스터디 1교시')['resolved_relative_path'],path.name)

    def test_conflicting_nas_file_is_not_overwritten(self):
        job=self.select(auto_title=True)
        other=self.nas/(self.path.stem+'_강의.wav');other.write_bytes(b'precious')
        with self.assertRaises(ValueError):rename_selected(self.store,job['id'],'강의')
        self.assertEqual(other.read_bytes(),b'precious')
        self.assertEqual(digest(self.path),self.sha)

    def test_cancel_blocks_nas_rename(self):
        job=self.select(auto_title=True)
        self.store.change_job(job['id'],cancel_requested=1)
        with self.assertRaises(ValueError):rename_selected(self.store,job['id'],'강의')
        self.assertTrue(self.path.exists())

    def test_sync_option_required_before_queuing_rename(self):
        self.cfg['sync_rename_ready']=False
        with self.assertRaises(ValueError):self.select(auto_title=True)
        self.assertEqual(self.store.jobs(),[])

    def test_reappeared_phone_copy_has_separate_unselected_identity(self):
        job=self.select(auto_title=True)
        rename_selected(self.store,job['id'],'강의')
        self.path.write_bytes(b'only-synthetic-test')
        scan(self.store,self.cfg)
        self.assertEqual(len(self.store.assets()),2)
        self.assertEqual(len({a['id'] for a in self.store.assets()}),2)
        self.assertEqual(len(self.store.jobs()),1)

    def test_rename_journal_recovers_after_move_before_commit(self):
        job=self.select(auto_title=True)
        target=self.nas/(self.path.stem+'_강의.wav')
        with self.store.db() as db:
            db.execute('INSERT INTO renames(job_id,asset_id,source_sha,original_path,previous_path,renamed_path,mtime_ns,size,status) VALUES(?,?,?,?,?,?,?,?,?)',
                       (job['id'],job['asset_id'],job['source_sha'],self.path.name,self.path.name,target.name,self.mtime,self.path.stat().st_size,'prepared'))
        os.rename(self.path,target)
        updated=rename_selected(self.store,job['id'],'강의')
        self.assertEqual(approved_source(updated,self.cfg),target)

    def test_plaud_name_original_names_and_full_raw_are_preserved(self):
        job=self.select(auto_title=True)
        job=rename_selected(self.store,job['id'],'예시스터디 1교시')
        segments=[{'start':1,'end':5,'text':'원문 문장은 고치지 않습니다.'}]
        out=publish(self.store,job,segments,{},note={'title':'예시스터디 1교시','summary':'## 핵심 요약\n요약임.'})
        self.assertEqual(out.name,'20261031-1641 [수강] 예시스터디 1교시.md')
        self.assertEqual(out.stat().st_mtime_ns,self.mtime)
        content=out.read_text('utf-8')
        self.assertIn('originalAudioFilename:',content)
        self.assertIn('audioFilename:',content)
        body=content.split('## Original Content\n',1)[1]
        self.assertEqual(body,'### 00:00:01\n\n원문 문장은 고치지 않습니다.\n')
        self.assertIn(hashlib.sha256(body.encode()).hexdigest(),content)

    def test_recording_dates_are_validated_without_inventing(self):
        self.assertIsNone(recording_time('20261399-250000.wav'))
        self.assertEqual(recording_time('2016-1031-164103_번호.wav').year,2016)

    def test_blank_title_hint_can_use_automatic_title(self):
        job=self.select(title='',auto_title=True)
        self.assertEqual(job['params']['title'],self.path.stem)
        segments=[{'start':0,'end':1,'text':'예시스터디 강의입니다.'}]
        result=compose(self.store,job,segments,lambda p:json.dumps({
            'title':'예시스터디','title_evidence':'예시스터디','summary':''}))
        self.assertEqual(result['title'],'예시스터디')

    def test_ai_answer_is_cached_before_parse_and_only_one_call(self):
        job=self.select(auto_title=True)
        calls=[]
        def generate(prompt):
            calls.append(prompt)
            return json.dumps({'title':'예시스터디 1교시','title_evidence':'예시스터디 1교시','summary':''})
        segments=[{'start':0,'end':5,'text':'오늘은 예시스터디 1교시입니다.'}]
        first=compose(self.store,job,segments,generate)
        second=compose(self.store,job,segments,lambda p:self.fail('cached call repeated'))
        self.assertEqual(first,second);self.assertEqual(len(calls),1)

    def test_malformed_ai_answer_preserved_without_automatic_regeneration(self):
        job=self.select(auto_title=True)
        segments=[{'start':0,'end':1,'text':'시험'}]
        with self.assertRaises(ValueError):compose(self.store,job,segments,lambda p:'broken json')
        with self.assertRaises(ValueError):compose(self.store,job,segments,lambda p:self.fail('unexpected generation'))

    def test_title_without_verbatim_evidence_cannot_rename(self):
        job=self.select(auto_title=True)
        answer=json.dumps({'title':'추측한 강좌','title_evidence':'실제로 없는 발언','summary':''})
        with self.assertRaises(ValueError):compose(self.store,job,[{'start':0,'end':1,'text':'시험'}],lambda p:answer)
        self.assertTrue(self.path.exists())

    def test_explicit_title_correction_reuses_saved_asr_and_preserves_audio(self):
        job=self.select(auto_title=True)
        job=rename_selected(self.store,job['id'],'해자 유형 찾기')
        rows=[{'start':0,'end':5,'text':'첫 문장입니다.'},{'start':5,'end':10,'text':'둘째 문장입니다.'}]
        note={'title':'해자 유형 찾기','summary':'## 핵심 요약\n정리함.'}
        old=publish(self.store,job,rows,{},note=note)
        work=self.home/'work'/job['id']
        atomic_json(work/'result.json',{'dedupe_key':job['dedupe_key'],'source_sha':job['source_sha'],'segments':rows,'versions':{}})
        atomic_json(work/'note-result.json',note)
        updated=revise_completed(self.store,job['id'],'예시스터디 오프라인 1교시-해자 유형 찾기')
        self.assertFalse(old.exists())
        self.assertIn('예시스터디 오프라인 1교시-해자 유형 찾기',updated.name)
        fresh=self.store.job(job['id']);audio=approved_source(fresh,self.cfg)
        self.assertEqual(digest(audio),self.sha)
        self.assertEqual(audio.stat().st_mtime_ns,self.mtime)
        self.assertIn('첫 문장입니다. 둘째 문장입니다.',updated.read_text('utf-8'))
        self.assertEqual(json.loads((work/'revision.json').read_text('utf-8'))['status'],'completed')

    def test_title_correction_preserves_user_edits(self):
        job=self.select(auto_title=True)
        note=publish(self.store,job,[{'start':0,'end':1,'text':'원문'}],{},note={'title':'강의'})
        note.write_text('사용자 편집',encoding='utf-8')
        with self.assertRaises(ValueError):revise_completed(self.store,job['id'],'새 강의')
        self.assertEqual(note.read_text('utf-8'),'사용자 편집')
        self.assertTrue(self.path.exists())

    def test_title_editor_updates_file_and_work_title_together(self):
        from app.web import create_app
        job=self.select(title='예시스터디 오프라인 1교시',auto_title=True)
        job=rename_selected(self.store,job['id'],'예시스터디 오프라인 1교시-해자')
        rows=[{'start':0,'end':5,'text':'원문은 그대로입니다.'}]
        note={'title':'예시스터디 오프라인 1교시-해자','summary':'정리함.'}
        publish(self.store,job,rows,{},note=note)
        work=self.home/'work'/job['id']
        atomic_json(work/'result.json',{'dedupe_key':job['dedupe_key'],'source_sha':job['source_sha'],'segments':rows,'versions':{}})
        atomic_json(work/'note-result.json',note)
        client=create_app(self.home).test_client();client.get('/')
        with client.session_transaction() as session:token=session['csrf']
        response=client.post(f"/api/jobs/{job['id']}/titles",json={
            'display_title':'예시스터디 오프라인 3교시',
            'file_title':'예시스터디 오프라인 3교시-해자'},headers={'X-CSRF-Token':token})
        self.assertEqual(response.status_code,200,response.get_data(as_text=True))
        changed=self.store.job(job['id'])
        self.assertEqual(changed['display_title'],'예시스터디 오프라인 3교시')
        self.assertIn('3교시',Path(changed['output_path']).name)
        self.assertIn('3교시',changed['resolved_relative_path'])
        self.assertEqual(digest(approved_source(changed,self.cfg)),self.sha)
