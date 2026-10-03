from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import patch

from app.tts.chatterbox_compat import apply_alignment_compat
from app.tts.chatterbox_manager import CHATTERBOX_PYTHON_CLI

from app.tts.chatterbox_cli import _load_model


class ChatterboxCLITests(unittest.TestCase):
    def test_multilingual_loader_matches_chatterbox_017_signature(self) -> None:
        class FakeMultilingualTTS:
            @classmethod
            def from_pretrained(cls, device: str):
                instance = cls()
                instance.device = device
                return instance

        package = types.ModuleType("chatterbox")
        package.__path__ = []
        module = types.ModuleType("chatterbox.mtl_tts")
        module.ChatterboxMultilingualTTS = FakeMultilingualTTS
        previous_package = sys.modules.get("chatterbox")
        previous_module = sys.modules.get("chatterbox.mtl_tts")
        sys.modules["chatterbox"] = package
        sys.modules["chatterbox.mtl_tts"] = module
        try:
            model = _load_model("multilingual_v3", "cpu")
        finally:
            if previous_package is None:
                sys.modules.pop("chatterbox", None)
            else:
                sys.modules["chatterbox"] = previous_package
            if previous_module is None:
                sys.modules.pop("chatterbox.mtl_tts", None)
            else:
                sys.modules["chatterbox.mtl_tts"] = previous_module

        self.assertIsInstance(model, FakeMultilingualTTS)
        self.assertEqual(model.device, "cpu")


if __name__ == "__main__":
    unittest.main()


class AlignmentCompatTests(unittest.TestCase):
    def test_guard_and_idempotence_in_both_entry_points(self):
        source = "def step(self, logits, next_token=None):\n    A = self.alignment\n    S = self.size\n    alignment_repetition = self.complete and (A[self.completed_at:, :-5].max(dim=1).values.sum() > 5)\n    return alignment_repetition\n"
        class EmptyReduction:
            def __getitem__(self, key):
                return self
            def max(self, **kwargs):
                raise AssertionError("Empty reduction must not run")
        for embedded in (False, True):
            namespace = {"__name__": "compat_test"}
            if embedded:
                exec(CHATTERBOX_PYTHON_CLI, namespace)
                apply = namespace["apply_alignment_compat"]
            else:
                apply = apply_alignment_compat
            functions = {}
            exec(source, functions)
            cls = type("Analyzer", (), {"step": functions["step"]})
            module = types.SimpleNamespace(AlignmentStreamAnalyzer=cls)
            with patch("inspect.getsource", return_value=source), patch("importlib.import_module", return_value=module):
                self.assertTrue(apply())
                first = cls.step
                self.assertTrue(apply())
                self.assertIs(cls.step, first)
            obj = cls()
            obj.alignment = EmptyReduction()
            obj.complete = True
            obj.completed_at = 0
            for size in range(1, 6):
                obj.size = size
                self.assertFalse(obj.step(None))


    def test_changed_upstream_method_is_left_unchanged(self):
        original = lambda self, logits: logits
        cls = type("Analyzer", (), {"step": original})
        module = types.SimpleNamespace(AlignmentStreamAnalyzer=cls)
        with patch("inspect.getsource", return_value="def step(self, logits): return logits"), patch("importlib.import_module", return_value=module):
            self.assertFalse(apply_alignment_compat())
        self.assertIs(cls.step, original)
