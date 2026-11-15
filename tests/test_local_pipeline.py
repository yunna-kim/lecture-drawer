from pathlib import Path
import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from app.local_pipeline import compose_hierarchical, split_source, parse_facts, referenced_facts, quality_issues, render_structured


class LocalPipelineTest(unittest.TestCase):
    def test_unready_long_local_result_is_blocked_at_app_entry(self):
        from app.note_ai import compose
        job={'id':'fixture','relative_path':'lecture.wav','params':{
            'title':'강좌','title_context':'강좌','hints':'','auto_title':True,
            'note_template':{'id':'note-lecture','body':'## 핵심 요약\n{transcript}'},
            'note_ai':{'provider':'ollama'}}}
        with patch('app.local_pipeline.compose_hierarchical',return_value={'quality_issues':['섹션 누락']}) as run:
            with self.assertRaises(ValueError):compose(SimpleNamespace(home=Path('.')),job,[{'start':0,'end':1,'text':'긴 원문 '*5000}])
        run.assert_called_once()

    def test_missing_sections_and_context_only_title_are_not_ready(self):
        template={'body':'## 핵심 요약\n## 주요 내용\n## Q&A'}
        value={'summary':'# 핵심 요약\n요약','title':'강좌 3교시'}
        issues=quality_issues(value,template,'강좌 3교시')
        self.assertEqual(len(issues),3)
        self.assertEqual(quality_issues({'title':'강좌 3교시 수면','summary':'# 핵심 요약\n## 주요 내용\n## Q&A'},template,'강좌 3교시'),[])

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=SimpleNamespace(home=Path(self.tmp.name))
        self.segments=[{'start':0,'end':600,'text':'수면과 스트레스는 서로 영향을 줍니다. '*500}]
        self.job={'id':'fixture','relative_path':'lecture.wav','params':{
            'title':'시험 강좌','title_context':'시험 강좌','hints':'', 'auto_title':True,
            'note_template':{'id':'raw'},'note_ai':{'model':'qwen3.5:4b','model_digest':'fixture'}}}
        self.calls=0

    def generate(self,prompt):
        self.calls+=1
        if '전사 문장:\n' in prompt:
            return json.dumps({'facts':[{'point':'수면과 스트레스의 관계','ref':1}]},ensure_ascii=False)
        return json.dumps({'title':'시험 강좌 수면과 스트레스','title_evidence_ref':1,'topic':'수면과 스트레스'},ensure_ascii=False)

    def test_utf8_chunks_preserve_every_character_and_source_range(self):
        rows=[{'start':3,'end':5,'text':'한글 😀\n '*3000},{'start':6,'end':9,'text':' 마지막 구간'}]
        chunks=split_source(rows,1000)
        self.assertEqual(''.join(c['text'] for c in chunks),''.join(x['text'] for x in rows))
        self.assertTrue(all(len(c['text'].encode())<=1000 for c in chunks))
        self.assertEqual(chunks[0]['start'],3);self.assertEqual(chunks[-1]['end'],9)

    def test_full_source_is_covered_and_repeat_uses_cached_calls(self):
        first=compose_hierarchical(self.store,self.job,self.segments,self.generate)
        self.assertGreater(first['generation']['source_chunks'],1)
        self.assertEqual(first['generation']['source_chunks'],first['generation']['covered_chunks'])
        calls=self.calls
        second=compose_hierarchical(self.store,self.job,self.segments,self.generate)
        self.assertEqual(second['title'],first['title'])
        self.assertEqual(second['generation']['new_calls'],0)
        self.assertEqual(self.calls,calls)

    def test_unverified_quote_is_rejected_without_automatic_regeneration(self):
        calls=[]
        def invalid(prompt):calls.append(prompt);return '{"facts":[{"point":"false","ref":99999}]}'
        for _ in range(2):
            with self.assertRaises(ValueError):compose_hierarchical(self.store,self.job,self.segments,invalid)
        self.assertEqual(len(calls),1)

    def test_interrupted_stage_needs_explicit_retry(self):
        def interrupted(prompt):raise TimeoutError('fixture')
        with self.assertRaises(TimeoutError):compose_hierarchical(self.store,self.job,self.segments,interrupted)
        with self.assertRaises(ValueError):compose_hierarchical(self.store,self.job,self.segments,self.generate)
        self.assertEqual(self.calls,0)
        (self.store.home/'work'/'fixture'/'allow-ai-retry').write_text('explicit retry')
        self.assertTrue(compose_hierarchical(self.store,self.job,self.segments,self.generate)['title'])

    def test_reduction_cannot_invent_new_evidence(self):
        with self.assertRaises(ValueError):parse_facts('{"facts":[{"point":"x","quote":"original quote"}]}','original quote',{'different quote'})

    def test_recursive_reduction_preserves_all_chunk_provenance(self):
        reductions=[]
        def verbose(prompt):
            if '전사 문장:\n' in prompt:
                return json.dumps({'facts':[{'point':'구간 핵심 내용 '*30,'ref':1} for _ in range(6)]},ensure_ascii=False)
            if '부분 정리들을' in prompt:
                reductions.append(prompt)
                return json.dumps({'facts':[{'point':'통합 핵심','ref':1}]},ensure_ascii=False)
            return self.generate(prompt)
        result=compose_hierarchical(self.store,self.job,self.segments,verbose)
        self.assertTrue(reductions)
        self.assertEqual(result['generation']['source_chunks'],result['generation']['covered_chunks'])

    def test_quote_is_resolved_by_app_without_model_rewriting(self):
        source='실제 원문은 교정하지 않습니다.'
        result=referenced_facts('{"facts":[{"point":"내용","ref":1,"quote":"invented"}]}',[source],source)
        self.assertEqual(result[0]['quote'],source)

    def test_sections_are_rendered_in_template_order_without_inventing_absent_events(self):
        evidence=[{'quote':'수면과 스트레스는 서로 영향을 줍니다.'}]
        value={'title':'수면과 스트레스 시험 강좌','topic':'수면과 스트레스','title_evidence_ref':1,
               'section_0':[{'text':'상호 영향 관계','ref':1}],'section_1':[]}
        result=render_structured(value,['핵심 요약','Q&A'],evidence,'시험 강좌',True)
        self.assertEqual(result['summary'],'## 핵심 요약\n- 상호 영향 관계\n\n## Q&A\n- 선택된 정리 근거에서 확인되지 않음 · 원문 검토 필요.')
        self.assertEqual(quality_issues(result,{'body':'## 핵심 요약\n## Q&A'},'시험 강좌'),[])
        value['section_0'][0]['ref']=2
        with self.assertRaises(ValueError):render_structured(value,['핵심 요약','Q&A'],evidence,'시험 강좌',True)

    def test_context_only_topic_and_missing_sections_are_rejected(self):
        value={'title':'시험 강좌','topic':'시험 강좌','title_evidence_ref':1}
        with self.assertRaises(ValueError):render_structured(value,[],[{'quote':'원문 근거'}],'시험 강좌',True)
        value.update(title='시험 강좌 수면',topic='수면')
        with self.assertRaises(ValueError):render_structured(value,['Q&A'],[{'quote':'수면 원문 근거'}],'시험 강좌',True)

    def test_topic_may_paraphrase_title_without_forcing_prefix_order(self):
        result=render_structured({'title':'수면 개선 방법 시험 강좌','topic':'수면 개선의 실천 방법',
            'title_evidence_ref':1},[],[{'quote':'수면을 개선하는 방법입니다.'}],'시험 강좌',True)
        self.assertEqual(result['title'],'수면 개선 방법 시험 강좌')

    def test_voice_priority_yield_does_not_require_manual_ai_retry(self):
        from app.gpu_schedule import SummaryDeferred
        def deferred(prompt):raise SummaryDeferred('voice priority')
        with self.assertRaises(SummaryDeferred):compose_hierarchical(self.store,self.job,self.segments,deferred)
        self.assertTrue(compose_hierarchical(self.store,self.job,self.segments,self.generate)['title'])
