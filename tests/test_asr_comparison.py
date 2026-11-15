import unittest
from tools.benchmark_asr_models import character_errors


class ASRComparisonTest(unittest.TestCase):
    def test_insert_delete_and_substitute(self):
        self.assertEqual(character_errors('가나다','가다')['errors'],1)
        self.assertEqual(character_errors('가나다','가마라')['errors'],2)
        self.assertEqual(character_errors('가나','가나다')['errors'],1)

    def test_spacing_punctuation_are_ignored_but_negation_and_numbers_are_not(self):
        self.assertEqual(character_errors('잠을 잘 잤다.','잠을잘잤다')['cer'],0)
        self.assertGreater(character_errors('잠을 못 잤다','잠을 잘 잤다')['cer'],0)
        self.assertGreater(character_errors('오십 명','십 명')['cer'],0)

    def test_empty_reference_is_not_reported_as_zero_error(self):
        with self.assertRaises(ValueError):character_errors('','아무것도')


class ComputeProfileTest(unittest.TestCase):
    def test_gpu_low_memory_setting_is_preserved_and_cpu_uses_int8(self):
        from app.profiles import inference_compute_type
        self.assertEqual(inference_compute_type({'compute_type':'int8_float16'},'cuda'),'int8_float16')
        self.assertEqual(inference_compute_type({'compute_type':'int8_float16'},'cpu'),'int8')
        self.assertEqual(inference_compute_type({},'cuda'),'float16')
        with self.assertRaises(ValueError):inference_compute_type({'compute_type':'invalid'},'cuda')

    def test_compute_profiles_do_not_overwrite_existing_default(self):
        from pathlib import Path
        import tempfile
        from app.profiles import register_whisper
        from app.config import load_settings
        with tempfile.TemporaryDirectory() as tmp:
            model=Path(tmp)/'model';model.mkdir()
            for name in ('model.bin','config.json','tokenizer.json','vocabulary.json'):(model/name).write_bytes(b'fixture')
            home=Path(tmp)/'state'
            baseline=register_whisper(home,model,'baseline')
            low_memory=register_whisper(home,model,'low memory',compute_type='int8_float16')
            self.assertNotEqual(baseline,low_memory)
            settings=load_settings(home)
            self.assertEqual(settings['active_profile'],baseline)
            self.assertEqual(settings['profiles'][low_memory]['compute_type'],'int8_float16')
            with self.assertRaises(ValueError):register_whisper(home,model,'invalid','cpu','float16')
