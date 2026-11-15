from types import SimpleNamespace as N
import unittest
from app.segments import owned_segments


class SegmentBoundaryTest(unittest.TestCase):
    def test_words_crossing_boundary_have_single_owner(self):
        segment = N(words=[N(start=8, end=9, word="앞 문장."),
                           N(start=9, end=10, word=" 규칙적인"),
                           N(start=10, end=11, word=" 수면"),
                           N(start=11, end=12, word=" 습관입니다.")])
        first = owned_segments([segment], 0, 0, 10)
        second = owned_segments([segment], 0, 10, 20)
        self.assertEqual(first[0]["text"], "앞 문장. 규칙적인")
        self.assertEqual(second[0]["text"], "수면 습관입니다.")

    def test_real_repetitions_are_not_removed_by_text(self):
        segment = N(words=[N(start=1, end=2, word="중요합니다."), N(start=3, end=4, word=" 중요합니다.")])
        result = owned_segments([segment], 0, 0, 10)
        self.assertEqual(result[0]["text"], "중요합니다. 중요합니다.")

    def test_audio_offset_is_preserved(self):
        segment = N(words=[N(start=1, end=2, word="test")])
        self.assertEqual(owned_segments([segment], 300, 300, 600)[0]["start"], 301)

    def test_decode_context_outside_approved_range_is_not_output(self):
        segment = N(words=[N(start=1, end=2, word="outside")])
        self.assertEqual(owned_segments([segment], 0, 3, 10), [])
