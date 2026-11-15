from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit, parse_qs

from app.catalog import approved_source, digest, safe_audio, scan
from app.config import load_settings, save_settings
from app.output import publish
from app.profiles import activate, register_whisper
from app.requests import cancel, enqueue, retry
from app.service import Service
from app.store import Store
from app.web import create_app


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home = self.root / "state"
        self.nas = self.root / "nas"
        self.wiki = self.root / "wiki"
        self.nas.mkdir()
        self.wiki.mkdir()
        self.cfg = load_settings(self.home)
        self.cfg.update(nas_root=str(self.nas), wiki_root=str(self.wiki), stable_seconds=0)
        self.profile = {"id": "test-profile", "label": "시험 전용", "engine": "faster-whisper",
                        "device": "cpu", "tested": False, "model_fingerprint": "test"}
        self.cfg.update(active_profile="test-profile", profiles={"test-profile": self.profile})
        save_settings(self.home, self.cfg)
        self.store = Store(self.home)
        self.lecture = self.nas / "lecture.wav"
        self.private = self.nas / "personal.wav"
        self.lecture.write_bytes(b"synthetic-lecture")
        self.private.write_bytes(b"synthetic-private")
        scan(self.store, self.cfg)

    def tearDown(self):
        self.tmp.cleanup()

    def asset(self, name="lecture.wav"):
        return next(a for a in self.store.assets() if a["relative_path"] == name)

    def select(self, **extra):
        return enqueue(self.store, self.cfg, [{"asset_id": self.asset()["id"], **extra}])[0]["id"]

    def test_scan_never_queues_or_writes_notes(self):
        self.assertEqual(len(self.store.assets()), 2)
        self.assertEqual(self.store.jobs(), [])
        self.assertEqual(list(self.wiki.iterdir()), [])
        self.assertFalse((self.home / "work").exists())

    def test_completion_clock_survives_title_edit_and_republish(self):
        identity = self.select()
        self.assertIsNone(self.store.job(identity)['completed_at'])
        with patch('app.store.time.time', return_value=1000):
            self.store.change_job(identity, status='completed')
        with patch('app.store.time.time', return_value=90000):
            self.store.change_job(identity, display_title='corrected title')
            self.store.change_job(identity, status='completed')
        job = self.store.job(identity)
        self.assertEqual(job['completed_at'], 1000)
        self.assertEqual(job['updated_at'], 90000)
        self.assertEqual(Store(self.home).job(identity)['completed_at'], 1000)

    def test_legacy_completion_clock_migrates_once(self):
        identity = self.select()
        with self.store.db() as db:
            db.execute('ALTER TABLE jobs DROP COLUMN completed_at')
            db.execute("UPDATE jobs SET status='completed',updated_at=1234 WHERE id=?", (identity,))
        migrated = Store(self.home)
        self.assertEqual(migrated.job(identity)['completed_at'], 1234)
        migrated.change_job(identity, display_title='later title')
        self.assertEqual(Store(self.home).job(identity)['completed_at'], 1234)

    def test_explicit_selection_only(self):
        identity = self.select()
        self.assertEqual(len(self.store.jobs()), 1)
        self.assertEqual(self.store.job(identity)["relative_path"], "lecture.wav")
        self.assertEqual(self.private.read_bytes(), b"synthetic-private")

    def test_batch_validation_atomic(self):
        with self.assertRaises(ValueError):
            enqueue(self.store, self.cfg, [{"asset_id": self.asset()["id"]}, {"asset_id": "missing"}])
        self.assertEqual(self.store.jobs(), [])

    def test_repeat_click_reuses_identical_job(self):
        self.assertEqual(self.select(), self.select())
        self.assertEqual(len(self.store.jobs()), 1)

    def test_changed_source_cannot_run(self):
        job = self.store.job(self.select())
        self.lecture.write_bytes(b"replacement")
        with self.assertRaises(ValueError):
            approved_source(job, self.cfg)

    def test_same_stats_different_hash_cannot_run(self):
        import os
        job = self.store.job(self.select())
        original = self.lecture.stat()
        self.lecture.write_bytes(b"x" * original.st_size)
        os.utime(self.lecture, ns=(original.st_atime_ns, original.st_mtime_ns))
        with self.assertRaises(ValueError):
            approved_source(job, self.cfg)

    def test_changed_after_listing_cannot_be_selected(self):
        self.lecture.write_bytes(b"a different file")
        with self.assertRaises(ValueError):
            self.select()

    def test_request_has_frozen_profile_and_settings(self):
        job = self.store.job(self.select())
        self.profile["label"] = "changed"
        self.assertEqual(job["params"]["profile"]["label"], "시험 전용")
        self.assertEqual(job["source_sha"], digest(self.lecture))

    def test_no_parent_or_arbitrary_file_access(self):
        for relative in ("../outside.wav", str(self.lecture), "lecture.wav:stream"):
            with self.assertRaises(ValueError):
                safe_audio(self.nas, relative)

    def test_nonfinite_ranges_rejected(self):
        for value in (float("nan"), float("inf"), -1):
            with self.assertRaises(ValueError):
                self.select(start=value)

    def test_partial_hidden_and_nested_files_ignored(self):
        (self.nas / "x.wav.part").write_bytes(b"partial")
        (self.nas / ".upload.wav").write_bytes(b"partial")
        (self.nas / "sub").mkdir()
        (self.nas / "sub" / "hidden.wav").write_bytes(b"private")
        scan(self.store, self.cfg)
        self.assertEqual(len(self.store.assets()), 2)

    def test_unavailable_share_does_not_erase_catalog(self):
        self.cfg["nas_root"] = str(self.root / "not-found")
        result = scan(self.store, self.cfg)
        self.assertFalse(result["connected"])
        self.assertEqual(len(self.store.assets()), 2)

    def test_cancelled_request_never_processed(self):
        identity = self.select()
        cancel(self.store, identity)
        Service(self.store).process_job(identity)
        self.assertEqual(self.store.job(identity)["status"], "cancelled")
        self.assertFalse((self.home / "work").exists())

    def test_recovery_preserves_consent_and_cancellation(self):
        identity = self.select()
        self.store.change_job(identity, status="running")
        self.store.recover()
        self.assertEqual(self.store.job(identity)["status"], "queued")
        self.store.change_job(identity, status="running", cancel_requested=1)
        self.store.recover()
        self.assertEqual(self.store.job(identity)["status"], "cancelled")

    def test_cancel_prevents_publish(self):
        identity = self.select()
        cancel(self.store, identity)
        with self.assertRaises(ValueError):
            publish(self.store, self.store.job(identity), [{"start": 0, "end": 1, "text": "test"}], {})
        self.assertEqual(list(self.wiki.iterdir()), [])

    def test_empty_recognition_does_not_create_note(self):
        with self.assertRaises(ValueError):
            publish(self.store, self.store.job(self.select()), [], {})
        self.assertEqual(list(self.wiki.iterdir()), [])

    def test_publish_idempotent_and_edit_protected(self):
        job = self.store.job(self.select())
        segments = [{"start": 2, "end": 4, "text": "시험용 전사 원문"}]
        path = publish(self.store, job, segments, {})
        self.assertEqual(path, publish(self.store, job, segments, {}))
        self.assertEqual(len(list(self.wiki.glob("*.md"))), 1)
        self.assertIn('sourceKind: "local-recording-transcript"', path.read_text("utf-8"))
        payload = path.read_text("utf-8").split("## Original Content\n", 1)[1]
        self.assertIn(hashlib.sha256(payload.encode()).hexdigest(), path.read_text("utf-8"))
        path.write_text("사용자 수정", encoding="utf-8")
        with self.assertRaises(ValueError):
            publish(self.store, job, segments, {})
        self.assertEqual(path.read_text("utf-8"), "사용자 수정")
        self.assertEqual(self.private.read_bytes(), b"synthetic-private")

    def test_segment_request_is_separate_job(self):
        full = self.select()
        section = self.select(start=2, end=5)
        self.assertNotEqual(full, section)

    def test_model_activation_requires_idle_and_review(self):
        identity = self.select()
        with self.assertRaises(ValueError):
            activate(self.home, self.store, "test-profile", identity)

    def test_http_security_and_safe_metadata(self):
        app = create_app(self.home)
        client = app.test_client()
        self.assertEqual(client.get("/").status_code, 200)
        self.assertEqual(client.post("/api/jobs", json={"selections": []}).status_code, 403)
        self.assertEqual(client.get("/", headers={"Host": "evil.example"}).status_code, 403)
        self.assertEqual(client.get("/", environ_overrides={"REMOTE_ADDR": "192.168.0.5"}).status_code, 403)
        status = client.get("/api/status")
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.headers["Cache-Control"], "no-store")
        self.assertNotIn("session.key", status.get_data(as_text=True))
        with client.session_transaction() as session:
            token = session["csrf"]
        response = client.post("/api/jobs", json={"selections": [{"asset_id": self.asset()["id"]}]}, headers={"X-CSRF-Token": token})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.store.jobs()), 1)
        self.assertEqual(client.post("/api/scan", json={}, headers={"X-CSRF-Token": token,"Origin": "https://evil.example"}).status_code, 403)

    def test_team_preview_page_and_api_are_local_read_only(self):
        client=create_app(self.home).test_client()
        page=client.get('/?tab=team')
        self.assertIn(b'data-tab="team"',page.data)
        status=client.get('/api/team-preview')
        self.assertEqual(status.status_code,200)
        self.assertFalse(status.json['processing_enabled'])
        self.assertEqual(client.get('/api/team-preview',headers={'Host':'evil.example'}).status_code,403)
        self.assertEqual(client.get('/api/team-preview',environ_overrides={'REMOTE_ADDR':'192.168.0.7'}).status_code,403)
        self.assertEqual(self.store.jobs(),[])

    def test_open_saved_note_uses_obsidian_and_encodes_filename(self):
        identity=self.select()
        path=self.wiki/'한글 강의 #1 & 50%.md'
        path.write_text('saved fixture',encoding='utf-8')
        before=digest(path)
        self.store.change_job(identity,status='completed',output_path=str(path))
        client=create_app(self.home).test_client();client.get('/')
        with client.session_transaction() as session:token=session['csrf']
        with patch('app.web.os.startfile') as open_app:
            response=client.post(f'/api/jobs/{identity}/open',json={},headers={'X-CSRF-Token':token})
        self.assertEqual(response.status_code,200)
        uri=urlsplit(open_app.call_args.args[0]);query=parse_qs(uri.query)
        self.assertEqual(uri.scheme,'obsidian')
        self.assertEqual(uri.netloc,'open')
        self.assertEqual(query['path'],[path.resolve().as_posix()])
        self.assertEqual(query['paneType'],['tab'])
        self.assertEqual(digest(path),before)

    def test_open_note_outside_output_folder_is_blocked(self):
        identity=self.select()
        path=self.home/'outside.md';path.write_text('fixture',encoding='utf-8')
        self.store.change_job(identity,status='completed',output_path=str(path))
        client=create_app(self.home).test_client();client.get('/')
        with client.session_transaction() as session:token=session['csrf']
        with patch('app.web.os.startfile') as open_app:
            response=client.post(f'/api/jobs/{identity}/open',json={},headers={'X-CSRF-Token':token})
        self.assertEqual(response.status_code,403);open_app.assert_not_called()

    def test_job_title_can_change_without_changing_request_or_audio(self):
        identity=self.select();before=self.store.job(identity)['params']
        client=create_app(self.home).test_client();client.get('/')
        with client.session_transaction() as session:token=session['csrf']
        response=client.post(f'/api/jobs/{identity}/titles',json={'display_title':'예시스터디 3교시'},
                             headers={'X-CSRF-Token':token})
        self.assertEqual(response.status_code,200)
        self.assertEqual(self.store.job(identity)['display_title'],'예시스터디 3교시')
        self.assertEqual(self.store.job(identity)['params'],before)
        self.assertEqual(self.lecture.read_bytes(),b'synthetic-lecture')
        self.assertEqual(list(self.wiki.iterdir()),[])

    def test_completed_audio_is_classified_separately_from_pending_audio(self):
        identity=self.select();job=self.store.job(identity)
        publish(self.store,job,[{'start':0,'end':1,'text':'시험용 전사'}],{})
        rows={a['relative_path']:a for a in self.store.assets()}
        self.assertTrue(rows['lecture.wav']['transcribed'])
        self.assertFalse(rows['personal.wav']['transcribed'])

    def test_changed_audio_is_not_treated_as_previous_completed_recording(self):
        identity=self.select();job=self.store.job(identity)
        publish(self.store,job,[{'start':0,'end':1,'text':'시험용 전사'}],{})
        self.lecture.write_bytes(b'new recording with different bytes')
        scan(self.store,self.cfg)
        self.assertFalse(self.asset()['transcribed'])

    def cached_result(self, identity):
        job = self.store.job(identity)
        work = self.home / 'work' / identity
        work.mkdir(parents=True, exist_ok=True)
        result = {'source_sha': job['source_sha'], 'dedupe_key': job['dedupe_key'],
                  'segments': [{'start': 0, 'end': 1, 'text': 'cached fixture'}], 'versions': {}}
        (work / 'result.json').write_text(json.dumps(result), encoding='utf-8')
        return work

    def note_fixture(self, identity, module, work):
        self.assertEqual(module, 'app.note_runner')
        (work / 'note-result.json').write_text(json.dumps({'dedupe_key': self.store.job(identity)['dedupe_key']}), encoding='utf-8')
        return True

    def test_save_failure_retry_reuses_asr_without_duplicate_note(self):
        identity = self.select()
        work = self.cached_result(identity)
        original = (work / 'result.json').read_bytes()
        service = Service(self.store)
        with patch.object(service, 'execute', side_effect=self.note_fixture) as execute:
            with patch('app.output.tempfile.mkstemp', side_effect=OSError(28, 'disk full')):
                with self.assertRaises(OSError):
                    service.process_job(identity)
            self.assertEqual(list(self.wiki.iterdir()), [])
            self.store.change_job(identity, status='failed')
            retry(self.store, identity)
            service.process_job(identity)
        self.assertEqual(execute.call_count, 2)
        self.assertEqual(self.store.job(identity)['status'], 'completed')
        self.assertEqual((work / 'result.json').read_bytes(), original)
        self.assertEqual(len(list(self.wiki.glob('*.md'))), 1)

    def test_nas_loss_after_note_result_cannot_publish(self):
        identity = self.select()
        self.cached_result(identity)
        service = Service(self.store)
        def disconnect(*args):
            self.note_fixture(*args)
            self.lecture.rename(self.nas / 'offline.fixture')
            return True
        with patch.object(service, 'execute', side_effect=disconnect):
            with self.assertRaises(ValueError):
                service.process_job(identity)
        self.assertEqual(list(self.wiki.iterdir()), [])
        (self.nas / 'offline.fixture').rename(self.lecture)
        self.store.change_job(identity, status='failed')
        retry(self.store, identity)
        with patch.object(service, 'execute', side_effect=self.note_fixture):
            service.process_job(identity)
        self.assertEqual(self.store.job(identity)['status'], 'completed')

    def test_changed_audio_after_note_result_cannot_publish(self):
        identity = self.select()
        self.cached_result(identity)
        service = Service(self.store)
        def replace(*args):
            self.note_fixture(*args)
            self.lecture.write_bytes(b'replaced after ASR')
            return True
        with patch.object(service, 'execute', side_effect=replace):
            with self.assertRaises(ValueError):
                service.process_job(identity)
        self.assertEqual(list(self.wiki.iterdir()), [])

    def test_cancel_during_note_stage_cannot_publish(self):
        identity = self.select()
        self.cached_result(identity)
        service = Service(self.store)
        def cancel_note(*args):
            self.note_fixture(*args)
            cancel(self.store, identity)
            return True
        with patch.object(service, 'execute', side_effect=cancel_note):
            with self.assertRaises(ValueError):
                service.process_job(identity)
        self.assertEqual(list(self.wiki.iterdir()), [])

    def test_model_configuration_switch_and_rollback_preserve_existing_jobs(self):
        model = self.root / 'model-fixture'
        model.mkdir()
        for name in ('model.bin', 'config.json', 'tokenizer.json', 'vocabulary.json'):
            (model / name).write_bytes(b'fixture - not an executable model')
        first = register_whisper(self.home, model, 'first', 'cpu')
        cfg = load_settings(self.home)
        cfg['active_profile'] = first
        save_settings(self.home, cfg)
        second = register_whisper(self.home, model, 'second', 'auto')
        cfg = load_settings(self.home)
        identity = enqueue(self.store, cfg, [{'asset_id': self.asset()['id']}], second)[0]['id']
        frozen = self.store.job(identity)['params']
        self.store.change_job(identity, status='completed')
        with self.assertRaises(ValueError):
            activate(self.home, self.store, second, reviewed_job='unreviewed')
        self.assertEqual(activate(self.home, self.store, second, reviewed_job=identity), second)
        self.assertEqual(load_settings(self.home)['previous_profile'], first)
        self.assertEqual(activate(self.home, self.store, None, rollback=True), first)
        self.assertEqual(load_settings(self.home)['active_profile'], first)
        self.assertEqual(self.store.job(identity)['params'], frozen)

    def plan(self, transcribe, asset=None):
        asset=asset or self.asset()
        self.store.set_candidate(asset['id'],transcribe,asset['size'],asset['mtime_ns'])

    def test_new_recordings_have_no_automatic_transcription_plan(self):
        for asset in self.store.assets():
            self.assertFalse(asset['candidate'])
            self.assertFalse(asset['excluded'])
        self.assertEqual(self.store.jobs(),[])

    def test_plan_and_defer_survive_scan_and_restart_without_touching_audio(self):
        before=(digest(self.lecture),self.lecture.stat().st_mtime_ns)
        self.plan(True)
        self.assertTrue(self.asset()['candidate'])
        self.assertEqual(self.store.jobs(),[])
        self.plan(False)
        scan(self.store,self.cfg)
        reloaded=Store(self.home).asset(self.asset()['id'])
        self.assertFalse(reloaded['candidate'])
        self.assertTrue(reloaded['excluded'])
        self.plan(True)
        self.assertTrue(self.asset()['candidate'])
        self.assertFalse(self.asset()['excluded'])
        self.assertEqual((digest(self.lecture),self.lecture.stat().st_mtime_ns),before)

    def test_changed_file_does_not_inherit_previous_plan(self):
        old=self.asset();self.plan(True)
        self.lecture.write_bytes(b'changed synthetic lecture data')
        scan(self.store,self.cfg)
        self.assertFalse(self.asset()['candidate'])
        self.assertFalse(self.asset()['excluded'])
        with self.assertRaises(ValueError):self.plan(False,old)

    def test_deferred_recording_cannot_be_enqueued_from_stale_selection(self):
        self.plan(False)
        with patch('app.requests.digest') as read_audio:
            with self.assertRaisesRegex(ValueError,'전사 예정'):self.select()
        read_audio.assert_not_called()
        self.assertEqual(self.store.jobs(),[])

    def test_active_and_completed_recordings_cannot_be_reclassified(self):
        self.plan(True);identity=self.select()
        for status in ('queued','running','saving','waiting_ai','completed'):
            self.store.change_job(identity,status=status)
            with self.assertRaises(ValueError):self.plan(False)
        self.assertTrue(self.asset()['candidate'])

    def test_retry_of_deferred_failed_job_requires_replanning(self):
        identity=self.select();self.store.change_job(identity,status='failed')
        self.plan(False)
        with self.assertRaisesRegex(ValueError,'전사 예정'):retry(self.store,identity)
        self.plan(True);retry(self.store,identity)
        self.assertEqual(self.store.job(identity)['status'],'queued')

    def test_plan_api_requires_csrf_and_exact_file_metadata(self):
        client=create_app(self.home).test_client();client.get('/')
        asset=self.asset();body={'transcribe':True,'size':asset['size'],'mtime_ns':asset['mtime_ns']}
        url=f"/api/assets/{asset['id']}/plan"
        self.assertEqual(client.post(url,json=body).status_code,403)
        with client.session_transaction() as session:token=session['csrf']
        headers={'X-CSRF-Token':token}
        self.assertEqual(client.post(url,json={**body,'transcribe':'yes'},headers=headers).status_code,400)
        self.assertEqual(client.post(url,json={**body,'size':0},headers=headers).status_code,400)
        self.assertEqual(client.post(url,json=body,headers=headers).status_code,200)
        self.assertTrue(self.asset()['candidate'])
        self.assertEqual(self.store.jobs(),[])

    def test_plan_timestamp_roundtrips_as_text_without_javascript_precision_loss(self):
        asset=self.asset()
        exact=1793520000123456789
        with self.store.db() as db:db.execute('UPDATE assets SET mtime_ns=? WHERE id=?',(exact,asset['id']))
        row=self.asset()
        self.assertEqual(row['plan_mtime_ns'],str(exact))
        self.store.set_candidate(row['id'],True,row['size'],row['plan_mtime_ns'])
        self.assertTrue(self.asset()['candidate'])
        with self.assertRaises(ValueError):self.store.set_candidate(row['id'],False,row['size'],str(int(float(exact))))

    def test_presentation_hides_other_recordings_and_restores_without_deleting(self):
        first=self.select()
        second=enqueue(self.store,self.cfg,[{'asset_id':self.asset('personal.wav')['id'],'title':'예시회의'}])[0]['id']
        self.store.meta('presentation',{'enabled':True,'visible_job_ids':[second],'fixed_titles':{second:'예시회의'}})
        client=create_app(self.home).test_client();client.get('/')
        result=client.get('/api/status').get_json()
        self.assertTrue(result['presentation'])
        self.assertEqual([j['id'] for j in result['jobs']],[second])
        self.assertEqual(len(result['assets']),1)
        self.assertEqual(result['jobs'][0]['display_title'],'personal')
        self.assertEqual(result['assets'][0]['relative_path'],'personal.wav')
        self.assertNotIn(str(self.nas),json.dumps(result,ensure_ascii=False))
        self.assertEqual(client.get(f'/api/jobs/{first}/result').status_code,403)
        self.assertEqual(client.get('/api/audio/'+self.asset()['id']).status_code,403)
        team=client.get('/api/team-preview')
        self.assertEqual(team.status_code,200)
        self.assertEqual(team.get_json()['reports'],[])
        self.assertNotIn(str(self.home.parent),json.dumps(team.get_json()))
        self.assertEqual(client.post('/api/scan',json={}).status_code,403)
        with client.session_transaction() as session:token=session['csrf']
        self.assertEqual(client.post('/api/presentation/exit',json={},headers={'X-CSRF-Token':token}).status_code,200)
        self.assertEqual(len(client.get('/api/status').get_json()['jobs']),2)
        self.assertEqual(len(self.store.jobs()),2)

    def test_presentation_can_show_other_audio_while_hiding_transcribed_jobs(self):
        hidden=self.select()
        visible=self.asset('personal.wav')['id']
        self.store.meta('presentation',{'enabled':True,'visible_job_ids':[],
                                      'visible_asset_ids':[visible]})
        client=create_app(self.home).test_client();client.get('/')
        result=client.get('/api/status').get_json()
        self.assertEqual(result['jobs'],[])
        self.assertEqual([a['id'] for a in result['assets']],[visible])
        response=client.get('/api/audio/'+visible)
        self.assertEqual(response.status_code,200)
        response.close()
        self.assertEqual(client.get('/api/audio/'+self.asset()['id']).status_code,403)
        self.assertEqual(client.get(f'/api/jobs/{hidden}/result').status_code,403)
        self.assertEqual(len(self.store.jobs()),1)

    def test_fixed_title_does_not_change_original_request_or_create_new_job(self):
        from app.presentation import fixed_title_job
        self.cfg['note_ai']={'provider':'claude-subscription','approval':'fixture-only-no-generation'}
        identity=self.select(auto_title=True)
        original=self.store.job(identity)
        self.store.meta('fixed_note_titles',{identity:'예시회의'})
        effective,title=fixed_title_job(self.store,original)
        self.assertEqual(title,'예시회의')
        self.assertFalse(effective['params']['auto_title'])
        self.assertTrue(self.store.job(identity)['params']['auto_title'])
        self.assertEqual(len(self.store.jobs()),1)

    def test_presentation_preserves_real_audio_and_note_filenames(self):
        from app.presentation import filtered_status
        data={'jobs':[{'id':'selected','asset_id':'audio','relative_path':'original.m4a',
              'params':{'title':'예시회의','profile':{'id':'fixture','label':'test'}},
              'output_path':'private/20260101-1000 [수강] 예시회의.md'}],
              'assets':[{'id':'audio','relative_path':'20260101-100000 테스트녹음_예시회의.m4a'}],
              'settings':{'nas_root':'private','wiki_root':'private'},'catalog':{}}
        self.store.meta('presentation',{'enabled':True,'visible_job_ids':['selected'],'fixed_titles':{'selected':'예시회의'}})
        value=filtered_status(self.store,data)
        self.assertEqual(value['assets'][0]['relative_path'],'20260101-100000 테스트녹음_예시회의.m4a')
        self.assertEqual(value['jobs'][0]['display_title'],'20260101-100000 테스트녹음_예시회의')
        self.assertEqual(value['jobs'][0]['output_path'],'20260101-1000 [수강] 예시회의.md')
        self.assertEqual(data['jobs'][0]['output_path'],'private/20260101-1000 [수강] 예시회의.md')

    def test_presentation_alias_hides_titles_without_renaming_originals(self):
        from app.presentation import filtered_status
        identity=self.select(title='private-title')
        job=self.store.job(identity)
        self.store.change_job(identity,status='completed',output_path='private-title.md',
                              progress={'message':'private-title saved'})
        self.store.meta('presentation',{'enabled':True,'visible_job_ids':[identity],
                                      'display_aliases':{identity:'전사 예시 1'}})
        data={'jobs':self.store.jobs(),'assets':self.store.assets(),
              'settings':{'nas_root':'private','wiki_root':'private'},'catalog':{}}
        result=filtered_status(self.store,data)
        self.assertEqual(result['jobs'][0]['display_title'],'전사 예시 1')
        self.assertEqual(result['jobs'][0]['params']['title'],'전사 예시 1')
        self.assertEqual(result['jobs'][0]['output_path'],'전사 예시 1.md')
        self.assertEqual(result['assets'][0]['relative_path'],'전사 예시 1.wav')
        self.assertNotIn('private-title',json.dumps(result))
        self.assertEqual(self.store.job(identity)['params'],job['params'])
        self.assertEqual(self.store.job(identity)['relative_path'],job['relative_path'])

    def test_worker_prioritizes_new_voice_over_deferred_summary(self):
        first=self.select();self.store.change_job(first,status='queued_summary')
        second=enqueue(self.store,self.cfg,[{'asset_id':self.asset('personal.wav')['id']}])[0]['id']
        service=Service(self.store);seen=[]
        def run(identity):seen.append(identity);service.stop_event.set()
        with patch.object(service,'process_job',side_effect=run):service.worker_loop()
        self.assertEqual(seen,[second])
        cancel(self.store,first)
        self.assertEqual(self.store.job(first)['status'],'cancelled')

    def test_local_summary_yields_to_other_queued_voice_and_keeps_transcript(self):
        from app.gpu_schedule import SummaryDeferred
        self.cfg['note_ai']={'provider':'ollama'}
        identity=self.select()
        job=self.store.job(identity);work=self.home/'work'/identity;work.mkdir(parents=True)
        (work/'result.json').write_text(json.dumps({'source_sha':job['source_sha'],'dedupe_key':job['dedupe_key'],
            'segments':[{'start':0,'end':1,'text':'fixture'}],'versions':{}}),encoding='utf-8')
        enqueue(self.store,self.cfg,[{'asset_id':self.asset('personal.wav')['id']}])
        with patch.object(Service,'execute') as execute:
            with self.assertRaises(SummaryDeferred):Service(self.store).process_job(identity)
        execute.assert_not_called()
        self.assertTrue((work/'result.json').exists())

    def test_voice_request_is_accepted_while_local_verification_is_running(self):
        with patch('app.web.Verification') as verification:
            verification.return_value.running=True
            client=create_app(self.home).test_client();client.get('/')
            with client.session_transaction() as session:token=session['csrf']
            response=client.post('/api/jobs',json={'selections':[{'asset_id':self.asset()['id']}]},
                                 headers={'X-CSRF-Token':token})
        self.assertEqual(response.status_code,200)
        self.assertEqual(self.store.jobs()[0]['status'],'queued')


if __name__ == "__main__":
    unittest.main()
