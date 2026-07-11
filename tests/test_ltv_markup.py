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

    def test_voice_accepts_optional_language_parameter(self) -> None:
        result = LTVMarkupCompiler.compile('{{voice "Mini" "Spanish"}}Hola.', "omnivoice")

        state = result.sections[0].segments[0].state
        self.assertEqual(state["voice"], "Mini")
        self.assertEqual(state["voice_language"], "Spanish")

    def test_omnivoice_voice_accepts_hyphen_language_suffix(self) -> None:
        for command in ('{{voice "Mini - Spa"}}Hola.', '{{voice "Mini-Spa"}}Hola.'):
            with self.subTest(command=command):
                result = LTVMarkupCompiler.compile(command, "omnivoice")
                state = result.sections[0].segments[0].state
                self.assertEqual(state["voice"], "Mini")
                self.assertEqual(state["voice_language"], "Spa")

    def test_hyphen_voice_names_can_select_language_for_qwen(self) -> None:
        result = LTVMarkupCompiler.compile('{{voice "Serena - Spanish"}}Hola.', "qwen")

        state = result.sections[0].segments[0].state
        self.assertEqual(state["voice"], "Serena")
        self.assertEqual(state["voice_language"], "Spanish")

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

    def test_volume_accepts_multiplier_percent_db_and_lufs(self) -> None:
        events = LTVMarkupParser.parse(
            "{{volume 0.8}}{{volume 80%}}{{volume -3db}}{{volume.normalize -16}}"
        ).events

        self.assertEqual([event.type for event in events], ["volume"] * 4)
        self.assertAlmostEqual(events[0].value, -1.938, places=2)
        self.assertAlmostEqual(events[1].value, -1.938, places=2)
        self.assertEqual(events[2].value, -3.0)
        self.assertEqual(events[3].attrs["mode"], "normalize_lufs")
        self.assertEqual(events[3].value, -16.0)

    def test_cmd_sendcommand_aliases(self) -> None:
        events = LTVMarkupParser.parse(
            '{{cmd "[laugh]"}}{{sendcommand "[gasp]"}}{{sendcomand "[sigh]"}}'
        ).events

        self.assertEqual(
            [event.value for event in events],
            [{"instruct": "[laugh]"}, {"instruct": "[gasp]"}, {"instruct": "[sigh]"}],
        )
        self.assertTrue(all(event.type == "config_override" for event in events))

    def test_cmd_accepts_json_fragment_overrides(self) -> None:
        event = LTVMarkupParser.parse(
            '{{cmd "instruct": "Warm voice.", "temperature": 0.7, "top_p": 0.9}}'
        ).events[0]

        self.assertEqual(event.type, "config_override")
        self.assertEqual(event.value["instruct"], "Warm voice.")
        self.assertEqual(event.value["temperature"], 0.7)
        self.assertEqual(event.value["top_p"], 0.9)

    def test_preset_accepts_json_fragment_overrides(self) -> None:
        event = LTVMarkupParser.parse(
            '{{preset "instruct": "Narrator tone.", "temperature": 0.6}}'
        ).events[0]

        self.assertEqual(event.type, "config_preset")
        self.assertEqual(event.value["instruct"], "Narrator tone.")
        self.assertEqual(event.value["temperature"], 0.6)

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
{{cmd "[gasp]"}}
No me gusta este sitio.
{{reset}}
"""
        result = LTVMarkupCompiler.compile(text, "qwen", random.Random(1))

        self.assertEqual(result.sections[0].title, "Capitulo 1")
        self.assertEqual(len(result.sections[0].segments), 2)
        self.assertEqual(result.sections[0].segments[0].pause_after_ms, 1200)
        self.assertEqual(
            result.sections[0].segments[1].state["config_overrides"],
            {"instruct": "[gasp]"},
        )
        self.assertNotIn("[gasp]", result.sections[0].segments[1].text)

    def test_model_commands_attach_to_next_segment_for_chatterbox(self) -> None:
        result = LTVMarkupCompiler.compile(
            'Text. {{cmd "[laugh]"}} Laughing text.',
            "chatterbox",
        )

        self.assertEqual(result.sections[0].segments[0].text, "Text.")
        self.assertEqual(
            result.sections[0].segments[1].state["config_overrides"],
            {"instruct": "[laugh]"},
        )

    def test_model_commands_attach_to_next_segment_for_piper(self) -> None:
        result = LTVMarkupCompiler.compile(
            'Text. {{cmd "[laugh]"}} Laughing text.',
            "piper",
        )

        self.assertEqual(result.sections[0].segments[0].text, "Text.")
        self.assertEqual(
            result.sections[0].segments[1].state["config_overrides"],
            {"instruct": "[laugh]"},
        )

    def test_preset_applies_until_reset_preset(self) -> None:
        result = LTVMarkupCompiler.compile(
            '{{preset "instruct": "Calm", "temperature": 0.6}}'
            "One. {{pause 1}}Two. "
            '{{reset.preset}}'
            "Three.",
            "qwen",
        )

        segments = result.sections[0].segments
        self.assertEqual(
            segments[0].state["config_overrides"],
            {"instruct": "Calm", "temperature": 0.6},
        )
        self.assertEqual(
            segments[1].state["config_overrides"],
            {"instruct": "Calm", "temperature": 0.6},
        )
        self.assertNotIn("config_overrides", segments[2].state)

    def test_cmd_overrides_preset_for_one_segment_only(self) -> None:
        result = LTVMarkupCompiler.compile(
            '{{preset "temperature": 0.6, "top_p": 0.9}}'
            "One. "
            '{{cmd "temperature": 0.9}}'
            "Two. {{pause 1}}"
            "Three.",
            "qwen",
        )

        segments = result.sections[0].segments
        self.assertEqual(
            segments[0].state["config_overrides"],
            {"temperature": 0.6, "top_p": 0.9},
        )
        self.assertEqual(
            segments[1].state["config_overrides"],
            {"temperature": 0.9, "top_p": 0.9},
        )
        self.assertEqual(
            segments[2].state["config_overrides"],
            {"temperature": 0.6, "top_p": 0.9},
        )


if __name__ == "__main__":
    unittest.main()
