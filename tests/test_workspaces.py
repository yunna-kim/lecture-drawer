import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from app.config import atomic_json, load_settings
from app.workspaces import prepare_team, require_team_allowed, preview_status
from app.store import Store
from app.service import Service
from app.requests import enqueue


class WorkspacesTest(unittest.TestCase):
    def test_preview_is_read_only_and_only_exposes_report_fields(self):
        with tempfile.TemporaryDirectory() as root:
            personal=Path(root)/'personal'
            self.assertFalse(preview_status(personal)['prepared'])
            self.assertFalse((Path(root)/'LectureDrawer-TeamPreview').exists())
            cfg=prepare_team(Path(root)/'LectureDrawer-TeamPreview')
            home=Path(cfg['state_home']);reports=home/'benchmarks';reports.mkdir()
            atomic_json(reports/'bench-fixture.json',{'status':'completed','start':0,'end':300,'seconds':12,
                'secret':'private-token','quality_issues':['needs review'],
                'result':{'title':'<script>fixture</script>','summary':'draft','generation':{'token':'private-token'}}})
            (reports/'bench-broken.json').write_text('{',encoding='utf8')
            before=(home/'settings.json').read_bytes()
            value=preview_status(personal)
            self.assertFalse(value['processing_enabled'])
            self.assertEqual(len(value['reports']),1)
            self.assertNotIn('private-token',json.dumps(value))
            self.assertEqual(value['reports'][0]['summary'],'draft')
            self.assertEqual((home/'settings.json').read_bytes(),before)

    def test_fresh_installation_does_not_inherit_author_paths(self):
        with tempfile.TemporaryDirectory() as root:
            home=Path(root)/'fresh'
            cfg=load_settings(home)
            self.assertEqual(Path(cfg['nas_root']),home/'input')
            self.assertEqual(Path(cfg['wiki_root']),home/'output')
            self.assertEqual(cfg['profiles'],{})
            self.assertNotIn('192.0.2.10',json.dumps(cfg))
            self.assertNotIn('ExampleVault',json.dumps(cfg))
            saved=(home/'settings.json').read_bytes()
            self.assertEqual(load_settings(home),cfg)
            self.assertEqual((home/'settings.json').read_bytes(),saved)

    def test_preview_is_separate_disabled_and_copies_no_personal_configuration(self):
        with tempfile.TemporaryDirectory() as root:
            personal = Path(root)/'personal'; personal.mkdir()
            atomic_json(personal/'settings.json', {'secret': 'private-fixture'})
            before=(personal/'settings.json').read_bytes()
            with patch('app.workspaces.state_home', return_value=personal):
                cfg=prepare_team()
                self.assertNotEqual(cfg['state_home'], str(personal))
                self.assertNotIn('secret', json.dumps(cfg))
                self.assertFalse(cfg['shared_processing_enabled'])
                self.assertFalse(cfg['isolation_verified'])
                self.assertEqual(cfg['profiles'], {})
                self.assertEqual(cfg['note_ai']['provider'], 'disabled')
                self.assertEqual(prepare_team(), cfg)
                for target in (personal,personal/'child'):
                    with self.assertRaises(ValueError):prepare_team(target)
            self.assertEqual((personal/'settings.json').read_bytes(), before)
            store=Store(Path(cfg['state_home']))
            with self.assertRaises(ValueError):enqueue(store,cfg,[])
            with self.assertRaises(ValueError):Service(store).start()
            with self.assertRaises(ValueError):Service(store).process_job('untrusted')
            self.assertEqual(store.jobs(), [])

    def test_enabled_alone_does_not_allow_shared_processing(self):
        for flags in ({},{'shared_processing_enabled': True},{'isolation_verified': True}):
            with self.assertRaises(ValueError):require_team_allowed({'mode':'team-preview',**flags})
        require_team_allowed({'mode':'personal'})

    def test_existing_personal_state_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as root:
            target=Path(root)/'existing';target.mkdir()
            atomic_json(target/'settings.json',{'schema':1,'nas_root':'private'})
            before=(target/'settings.json').read_bytes()
            with self.assertRaises(ValueError):prepare_team(target)
            self.assertEqual((target/'settings.json').read_bytes(),before)
