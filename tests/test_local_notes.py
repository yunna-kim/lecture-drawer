import unittest
from unittest.mock import patch
from app.local_notes import check_model, generate


class LocalNoteTest(unittest.TestCase):
    @patch("app.local_notes.request")
    def test_missing_model_does_not_download(self, request):
        request.return_value={'models':[]}
        with self.assertRaises(ValueError):check_model('qwen3.5:4b')
        self.assertEqual(request.call_count,1)

    @patch("app.local_notes.request")
    def test_cloud_model_is_rejected(self, request):
        request.side_effect=[{'models':[{'name':'qwen3.5:4b','digest':'abc'}]}, {'remote_host':'cloud'}]
        with self.assertRaises(ValueError):check_model('qwen3.5:4b')

    @patch("app.local_notes.check_model", return_value='abc')
    @patch("app.local_notes.request")
    def test_long_input_does_not_silently_truncate(self, request, check):
        with self.assertRaises(ValueError):generate('긴 강의'*5000,{'model':'qwen3.5:4b','model_digest':'abc'})
        request.assert_not_called()

    @patch("app.local_notes.check_model", return_value='abc')
    @patch("app.local_notes.request")
    def test_complete_local_response_and_fingerprint(self, request, check):
        request.return_value={'done':True,'done_reason':'stop','response':'{}'}
        result=generate('short fixture',{'model':'qwen3.5:4b','model_digest':'abc'})
        self.assertEqual(result['generation']['model_digest'],'abc')
        self.assertEqual(request.call_args.args[0],'/api/generate')

    @patch("app.local_notes.check_model", return_value='abc')
    @patch("app.local_notes.request")
    def test_json_schema_is_sent_to_local_runtime(self, request, check):
        request.return_value={'done':True,'done_reason':'stop','response':'{}'}
        schema={'type':'object','properties':{'title':{'type':'string'}},'required':['title']}
        generate('fixture',{'model':'qwen3.5:4b','model_digest':'abc'},schema=schema)
        self.assertEqual(request.call_args.args[1]['format'],schema)
