from pathlib import Path
from types import SimpleNamespace as NS
import tempfile
import unittest
from unittest.mock import patch
import sys

from app.setup import configure
from app.config import load_settings
from app import api_routes as api
from app.note_templates import freeze


class PublicSetupTest(unittest.TestCase):
    def test_installed_decoder_can_open_synthetic_audio(self):
        # Regression: PyAV 19 removed an argument used by faster-whisper 1.2.1.
        import wave
        from faster_whisper.audio import decode_audio
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'silent.wav'
            with wave.open(str(path),'wb') as wav:
                wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000)
                wav.writeframes(b'\0\0'*16000)
            decoded=decode_audio(str(path),sampling_rate=16000)
            self.assertEqual(len(decoded),16000)

    def test_pc_source_and_obsidian_output_are_configured_without_ai_or_rename(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'recordings';source.mkdir()
            cfg=configure(root/'state',source,root/'vault'/'강의서랍',8879)
            self.assertEqual(cfg['nas_root'],str(source.resolve()))
            self.assertEqual(cfg['port'],8879)
            self.assertFalse(cfg['rename_selected_audio'])
            self.assertFalse(cfg['sync_rename_ready'])
            self.assertEqual(cfg.get('note_ai',{}).get('provider','disabled'),'disabled')
            self.assertTrue(Path(cfg['wiki_root']).is_dir())

    def test_nested_output_is_rejected_before_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'recordings';source.mkdir()
            with self.assertRaises(ValueError):configure(root/'state',source,source/'notes')
            self.assertFalse((source/'notes').exists())

    def test_packaged_templates_do_not_need_author_vault(self):
        for name in ('note-lecture','note-meeting'):
            self.assertIn('{transcript}',freeze(name)['body'])

    def test_provider_keys_and_selected_routes_are_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp);cfg={}
            api.store_key(home,'sk-fixture-claude','claude-api')
            api.store_key(home,'sk-fixture-openai','openai-api')
            self.assertNotEqual(api.read_key(home),api.read_key(home,'openai-api'))
            api.register(cfg,'fixture-model','openai-api');api.use(cfg,'openai-api')
            self.assertTrue(api.route_status(cfg,home)['ready'])
            self.assertIsNone(api.credit_status(cfg,home))
            self.assertNotIn('sk-',str(api.route_status(cfg,home)))

    def test_openai_calls_responses_without_retries_or_server_storage(self):
        calls=[]
        def client(**kwargs):
            self.assertEqual(kwargs['max_retries'],0)
            def create(**args):
                calls.append(args)
                return NS(status='completed',output_text='fixture answer',model='fixture',usage=None)
            return NS(responses=NS(create=create))
        with tempfile.TemporaryDirectory() as tmp,patch.dict(sys.modules,{'openai':NS(OpenAI=client)}):
            home=Path(tmp);api.store_key(home,'sk-fixture','openai-api')
            result=api.generate('synthetic prompt',{'provider':'openai-api','model':'fixture'},home)
            self.assertEqual(result['answer'],'fixture answer')
            self.assertFalse(calls[0]['store'])
            self.assertNotIn('sk-fixture',(home/'api-usage.jsonl').read_text())

    def test_claude_stream_without_beta_or_fallback(self):
        calls=[]
        class Stream:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def get_final_message(self):
                return NS(stop_reason='end_turn',model='fixture',content=[NS(type='text',text='answer')],
                          usage=NS(model_dump=lambda:{'input_tokens':1,'output_tokens':1}))
        def client(**kwargs):
            self.assertEqual(kwargs['max_retries'],0)
            def stream(**args):calls.append(args);return Stream()
            return NS(messages=NS(stream=stream))
        with tempfile.TemporaryDirectory() as tmp,patch.dict(sys.modules,{'anthropic':NS(Anthropic=client)}):
            home=Path(tmp);api.store_key(home,'sk-fixture')
            self.assertEqual(api.generate('synthetic',{'provider':'claude-api','model':'fixture'},home)['answer'],'answer')
            self.assertNotIn('fallbacks',calls[0]);self.assertNotIn('betas',calls[0])

    def test_provider_failure_is_waiting_without_private_error_details(self):
        def fail(**kwargs):raise RuntimeError('private fixture key')
        with tempfile.TemporaryDirectory() as tmp,patch.dict(sys.modules,{'openai':NS(OpenAI=fail)}):
            home=Path(tmp);api.store_key(home,'sk-fixture','openai-api')
            with self.assertRaises(api.ApiWaiting) as got:
                api.generate('fixture',{'provider':'openai-api','model':'fixture'},home)
            self.assertNotIn('private fixture',str(got.exception))
