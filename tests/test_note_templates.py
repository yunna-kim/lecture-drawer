from pathlib import Path
import tempfile
import unittest
from app.note_templates import catalog, freeze, fill_prompt


class TemplateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.path = self.root / "note-lecture.md"
        self.path.write_text("---\nkind: summary\n---\n제목: {title}\n원문: {transcript}", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_frozen_prompt_survives_source_edit(self):
        snapshot = freeze("note-lecture", root=self.root)
        self.path.write_text("changed", encoding="utf-8")
        self.assertIn("{transcript}", snapshot["body"])

    def test_only_current_summary_templates_are_selectable(self):
        (self.root / "internal.md").write_text("---\nkind: internal\n---\nsecret", encoding="utf-8")
        (self.root / "old.md").write_text("---\nkind: summary\ndeprecated: true\n---\nold", encoding="utf-8")
        self.assertEqual([x["id"] for x in catalog(self.root)], ["raw", "note-lecture"])

    def test_custom_prompt_does_not_edit_source(self):
        old = self.path.read_bytes()
        self.assertEqual(freeze("note-lecture", "사용자 지시", self.root)["body"], "사용자 지시")
        self.assertEqual(self.path.read_bytes(), old)

    def test_prompt_substitution_keeps_source_verbatim(self):
        value = fill_prompt(freeze("note-lecture", root=self.root), title="{transcript}", hints="", transcript="{title} 입력")
        self.assertEqual(value, "제목: {transcript}\n원문: {title} 입력")

    def test_path_escape_rejected(self):
        with self.assertRaises(ValueError):
            freeze("../outside", root=self.root)
