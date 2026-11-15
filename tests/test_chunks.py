from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.chunks import pause_cut, plan_chunks


class ChunkPlanTest(unittest.TestCase):
    def test_cut_in_pause_before_target(self):
        log = "silence_start: 3.7\nsilence_end: 4.7 | silence_duration: 1"
        self.assertAlmostEqual(pause_cut(log, 5, 5, 10), 9.2)

    def test_missing_or_out_of_window_pause_uses_target(self):
        self.assertEqual(pause_cut("silence_start: 20\nsilence_end: 22", 0, 5, 10), 10)
        self.assertEqual(pause_cut("", 0, 5, 10), 10)

    @patch("app.chunks.subprocess.run")
    def test_plan_has_no_gaps_and_keeps_range(self, run):
        run.return_value = SimpleNamespace(stderr="silence_start: 2\nsilence_end: 3")
        plan = plan_chunks(Path("selected.wav"), 300, 332, 10, "ffmpeg")
        self.assertEqual(plan[0][0], 300)
        self.assertEqual(plan[-1][1], 332)
        for index, (left, right) in enumerate(plan):
            self.assertGreater(right, left)
            self.assertLessEqual(right - left, 10)
            if index:
                self.assertEqual(plan[index - 1][1], left)

    @patch("app.chunks.subprocess.run")
    def test_short_request_needs_no_probe(self, run):
        self.assertEqual(plan_chunks(Path("selected.wav"), 4, 8, 10, "ffmpeg"), [(4, 8)])
        run.assert_not_called()
