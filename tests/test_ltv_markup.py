from __future__ import annotations

import random
import unittest

from app.core.ltv_markup import LTVMarkupCompiler, LTVMarkupParser


class LTVMarkupParserTests(unittest.TestCase):
    def test_text_without_commands_returns_text_event(self) -> None:
        result = LTVMarkupParser.parse("Plain narration.")

        self.assertFalse(result.has_markup)
        self.assertEqual(len(result.events), 1)
        self.assertEqual(result.events[0].type, "text")
        self.assertEqual(result.events[0].value, "Plain narration.")

    def test_pause_ms_seconds_and_presets(self) -> None:
        self.assertEqual(
            LTVMarkupParser.parse("A {{pause 700ms}} B").events[1].value,
            700,
        )
        self.assertEqual(
            LTVMarkupParser.parse("A {{pause 0.7s}} B").events[1].value,
            700,
        )
        self.assertEqual(
            LTVMarkupParser.parse("A {{pause.long}} B").events[1].value,
            1200,
        )

    def test_pause_random_keeps_range(self) -> None:
        event = LTVMarkupParser.parse("A {{pause random 500 1200}} B").events[1]

        self.assertEqual(event.type, "pause")
        self.assertEqual(event.attrs["random_min_ms"], 500)
        self.assertEqual(event.attrs["random_max_ms"], 1200)

    def test_voice_with_quotes_and_character_role(self) -> None:
        event = LTVMarkupParser.parse('{{voice.character "Lucia"}}Hola').events[0]

        self.assertEqual(event.type, "voice")
        self.assertEqual(event.value, "Lucia")
        self.assertEqual(event.attrs["role"], "character")

    def test_commands_are_case_insensitive(self) -> None:
        events = LTVMarkupParser.parse('{{VOICE "Lucia"}}{{Lang ES}}').events

        self.assertEqual(events[0].type, "voice")
        self.assertEqual(events[0].value, "Lucia")
        self.assertEqual(events[1].type, "language")
        self.assertEqual(events[1].value, "es")

    def test_commands_accept_smart_quotes_and_unicode_dashes(self) -> None:
        events = LTVMarkupParser.parse('{{Voice "Serena – Spanish“}}').events

        self.assertEqual(events[0].type, "voice")
        self.assertEqual(events[0].value, "Serena - Spanish")

    def test_emotion_and_reset(self) -> None:
        events = LTVMarkupParser.parse("{{emotion whisper}}A{{reset.emotion}}").events

        self.assertEqual(events[0].type, "emotion")
        self.assertEqual(events[0].value, "whisper")
        self.assertEqual(events[2].type, "reset")
        self.assertEqual(events[2].value, "emotion")

    def test_cmd_sendcommand_aliases(self) -> None:
        events = LTVMarkupParser.parse(
            '{{cmd "[laugh]"}}{{sendcommand "[gasp]"}}{{sendcomand "[sigh]"}}'
        ).events

        self.assertEqual([event.value for event in events], ["[laugh]", "[gasp]", "[sigh]"])
        self.assertTrue(all(event.type == "model_command" for event in events))

    def test_pronunciation_alias(self) -> None:
        result = LTVMarkupCompiler.compile(
            '{{alias "GPT" "ge pe te"}}GPT is useful.',
            "piper",
        )

        self.assertEqual(result.sections[0].segments[0].text, "ge pe te is useful.")

    def test_music_and_sfx_events(self) -> None:
        events = LTVMarkupParser.parse(
            '{{music "dark.mp3"}}{{music.volume -20}}{{sfx "door.wav"}}{{music.stop}}'
        ).events

        self.assertEqual(
            [event.type for event in events],
            ["music_start", "music_volume", "sfx", "music_stop"],
        )

    def test_unknown_command_warns_and_is_not_text(self) -> None:
        result = LTVMarkupParser.parse('Hola {{voz "Maria"}}.')

        self.assertEqual(result.unknown_commands, ["voz"])
        self.assertIn("voice", result.warnings[0])
        self.assertNotIn("voz", "".join(str(event.value or "") for event in result.events))

    def test_complex_text_compiles_to_sections_segments_and_events(self) -> None:
        text = """
{{chapter "Capitulo 1"}}
{{voice.narrator}}
La casa llevaba anos abandonada.
{{pause.long}}
{{voice.character "Lucia"}}
{{emotion whisper}}
{{cmd "[gasp]"}}
No me gusta este sitio.
{{reset}}
"""
        result = LTVMarkupCompiler.compile(text, "qwen", random.Random(1))

        self.assertEqual(result.sections[0].title, "Capitulo 1")
        self.assertEqual(len(result.sections[0].segments), 2)
        self.assertEqual(result.sections[0].segments[0].pause_after_ms, 1200)
        self.assertEqual(
            result.sections[0].segments[1].state["model_instruction"],
            "[gasp]",
        )
        self.assertNotIn("[gasp]", result.sections[0].segments[1].text)

    def test_model_commands_remain_literal_for_chatterbox(self) -> None:
        result = LTVMarkupCompiler.compile('Text. {{cmd "[laugh]"}}', "chatterbox")

        self.assertIn("[laugh]", result.sections[0].segments[0].text)

    def test_model_commands_are_ignored_for_piper(self) -> None:
        result = LTVMarkupCompiler.compile('Text. {{cmd "[laugh]"}}', "piper")

        self.assertEqual(result.sections[0].segments[0].text, "Text.")
        self.assertIn('{{cmd "[laugh]"}}', result.ignored_commands)


if __name__ == "__main__":
    unittest.main()
