import re
import unittest
from app.paragraphs import group_segments
from app.naming import missing_context_terms
from app.note_ai import validate_answer


class ParagraphTest(unittest.TestCase):
    def test_short_segments_become_one_paragraph_without_lost_words(self):
        segments=[{'start':i*3,'end':i*3+2,'text':f'문장 {i}입니다.'} for i in range(12)]
        grouped=group_segments(segments)
        self.assertLess(len(grouped),len(segments))
        plain=lambda rows:re.sub(r'\s+','',''.join(r['text'] for r in rows))
        self.assertEqual(plain(grouped),plain(segments))
        self.assertEqual(grouped[0]['start'],0)
        self.assertEqual(grouped[-1]['end'],35)

    def test_long_speech_splits_at_sentence_boundaries(self):
        rows=[{'start':i*5,'end':i*5+4,'text':'강의의 중요한 내용입니다.'} for i in range(40)]
        grouped=group_segments(rows)
        self.assertGreater(len(grouped),1)
        self.assertLess(len(grouped),len(rows)//2)

    def test_context_may_appear_in_natural_order_without_being_prefix(self):
        self.assertEqual(missing_context_terms('예시스터디 오프라인 1교시','해자 유형 찾기: 예시스터디 1교시 오프라인 모임'),[])
        self.assertEqual(missing_context_terms('예시스터디 오프라인 1교시','예시스터디 오프라인 11교시'),['1교시'])

    def test_missing_course_context_is_rejected(self):
        import json
        raw=json.dumps({'title':'해자 유형 찾기','title_evidence':'해자 유형 찾기','summary':''})
        with self.assertRaises(ValueError):
            validate_answer(raw,'해자 유형 찾기',True,False,'예시스터디 오프라인 1교시')
