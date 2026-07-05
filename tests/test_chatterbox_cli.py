from __future__ import annotations

import sys
import types
import unittest

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
