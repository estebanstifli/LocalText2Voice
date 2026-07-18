from __future__ import annotations

import sqlite3
from pathlib import Path

from app.core.audio_pipeline import AudioGenerationOptions, AudioPipeline
from app.core.text_normalization import TextNormalizationStore, TextNormalizer


class _NoopEngine:
    def cancel_current(self) -> None:
        return


def test_english_dictionary_is_seeded_and_editable(tmp_path: Path) -> None:
    store = TextNormalizationStore(tmp_path / "normalization.sqlite3")
    entries = store.list_entries("en")

    assert len(entries) >= 50
    doctor = next(entry for entry in entries if entry.source == "Dr.")
    store.update_entry(
        doctor.id,
        category=doctor.category,
        source=doctor.source,
        replacement="Drive",
        enabled=False,
    )
    store.add_entry("en", "custom", "LTV", "Local Text to Voice")

    changed = store.list_entries("en")
    assert not next(entry for entry in changed if entry.source == "Dr.").enabled
    assert next(entry for entry in changed if entry.source == "LTV").replacement == "Local Text to Voice"

    store.reset_language("en")
    reset = store.list_entries("en")
    assert next(entry for entry in reset if entry.source == "Dr.").replacement == "Doctor"
    assert not any(entry.source == "LTV" for entry in reset)


def test_english_rules_cover_numbers_ordinals_money_units_dates_and_roman(tmp_path: Path) -> None:
    normalizer = TextNormalizer(db_path=tmp_path / "normalization.sqlite3")

    result = normalizer.normalize(
        "Dr. Smith paid $12.50 for 8 GB on 2026-07-18, in chapter XXI. "
        "It was 20% cheaper than the 3rd offer.",
        language="en",
    )

    assert result == (
        "Doctor Smith paid twelve dollars and fifty cents for eight gigabytes "
        "on July eighteenth, two thousand twenty-six, in chapter twenty-one. "
        "It was twenty percent cheaper than the third offer."
    )


def test_english_numeric_dates_use_the_same_spoken_form_for_common_separators(
    tmp_path: Path,
) -> None:
    normalizer = TextNormalizer(db_path=tmp_path / "normalization.sqlite3")

    result = normalizer.normalize(
        "From 7/18/2026 to 7.31.2026 or 7-31-2026.",
        language="en",
    )

    assert result == (
        "From seven/eighteen/two thousand twenty-six to "
        "seven/thirty-one/two thousand twenty-six or "
        "seven/thirty-one/two thousand twenty-six."
    )


def test_units_are_only_expanded_after_numbers_and_disabled_entries_do_not_apply(
    tmp_path: Path,
) -> None:
    store = TextNormalizationStore(tmp_path / "normalization.sqlite3")
    ai = next(entry for entry in store.list_entries("en") if entry.source == "AI")
    store.update_entry(
        ai.id,
        category=ai.category,
        source=ai.source,
        replacement=ai.replacement,
        enabled=False,
    )
    normalizer = TextNormalizer(store=store)

    result = normalizer.normalize(
        "AI needs 1 GB, but GB alone stays GB and in stays in.",
        language="en",
    )

    assert result == "AI needs one gigabyte, but GB alone stays GB and in stays in."


def test_auto_language_and_markup_commands_are_preserved(tmp_path: Path) -> None:
    normalizer = TextNormalizer(db_path=tmp_path / "normalization.sqlite3")
    source = "Mr. Fox {{pause 250}} owns 2 GPUs. {{play sfx/boom2.wav}}"

    assert normalizer.normalize(source, language="auto", language_hint="es-ES") == (
        "Mr. Fox {{pause 250}} owns dos GPUs. {{play sfx/boom2.wav}}"
    )
    assert normalizer.normalize(source, language="auto", language_hint="en-US") == (
        "Mister Fox {{pause 250}} owns two GPUs. {{play sfx/boom2.wav}}"
    )
    assert normalizer.normalize("A&B = 2", language="en") == "A and B equals two"


def test_starter_dictionaries_match_all_ui_languages(tmp_path: Path) -> None:
    store = TextNormalizationStore(tmp_path / "normalization.sqlite3")

    dictionaries = store.list_dictionaries()

    assert {dictionary.language for dictionary in dictionaries} == {
        "ar", "de", "en", "es", "fr", "hi", "it", "ja", "pt", "zh"
    }
    assert all(dictionary.is_builtin for dictionary in dictionaries)
    assert all(store.list_entries(dictionary.language) for dictionary in dictionaries)


def test_custom_dictionary_json_round_trip_and_legacy_import(tmp_path: Path) -> None:
    source_store = TextNormalizationStore(tmp_path / "source.sqlite3")
    source_store.create_dictionary("nl-nl", "Dutch (Netherlands)")
    source_store.add_entry("nl-nl", "abbreviations", "dhr.", "de heer")
    disabled_id = source_store.add_entry(
        "nl-nl", "custom", "LTV", "Local Text to Voice", enabled=False
    )

    payload = source_store.export_dictionary("nl-nl")
    assert payload["format"] == "localtext2voice-normalization-dictionary"
    assert any(
        entry["source"] == "LTV" and entry["enabled"] is False
        for entry in payload["entries"]
    )

    target_store = TextNormalizationStore(tmp_path / "target.sqlite3")
    language, count = target_store.import_dictionary(payload, mode="replace")
    assert (language, count) == ("nl-nl", 2)
    imported = target_store.list_entries("nl-nl")
    assert next(entry for entry in imported if entry.source == "dhr.").replacement == "de heer"
    assert not next(entry for entry in imported if entry.source == "LTV").enabled

    legacy = {
        "language": "nl-nl",
        "symbols": {"&": "en"},
        "internet": {".nl": " punt N L"},
    }
    target_store.import_dictionary(legacy, mode="merge")
    assert {entry.source for entry in target_store.list_entries("nl-nl")} >= {
        "dhr.", "LTV", "&", ".nl"
    }

    source_store.delete_entries([disabled_id])
    source_store.delete_dictionary("nl-nl")
    assert source_store.dictionary("nl-nl") is None


def test_spanish_rules_cover_numbers_money_units_dates_ordinals_and_roman(
    tmp_path: Path,
) -> None:
    normalizer = TextNormalizer(db_path=tmp_path / "normalization.sqlite3")

    result = normalizer.normalize(
        "El Dr. Ruiz pagó 12,50 € por 8 GB el 18/7/2026, obtuvo 20% y quedó 3.º en XXI.",
        language="es",
    )

    assert result == (
        "El Doctor Ruiz pagó doce euros con cincuenta céntimos por ocho gigabytes "
        "el dieciocho/siete/dos mil veintiséis, obtuvo veinte por ciento y quedó "
        "tercero en veintiuno."
    )


def test_hindi_uses_dictionary_rules_without_claiming_number_expansion(
    tmp_path: Path,
) -> None:
    normalizer = TextNormalizer(db_path=tmp_path / "normalization.sqlite3")

    assert normalizer.normalize("डॉ. राय ने 8 GB खरीदा।", language="hi") == (
        "डॉक्टर राय ने 8 गीगाबाइट खरीदा।"
    )


def test_automatic_rules_can_be_disabled_globally_without_disabling_dictionary(
    tmp_path: Path,
) -> None:
    normalizer = TextNormalizer(db_path=tmp_path / "normalization.sqlite3")
    source = (
        "Dr. Fox has 12, pays $20, gets 50%, carries 8 GB, ranks 3rd "
        "in XXI on 2026-07-18."
    )

    result = normalizer.normalize(
        source,
        language="en",
        rules={"enabled": False},
    )

    assert result == (
        "Doctor Fox has 12, pays $20, gets 50%, carries 8 GB, ranks 3rd "
        "in XXI on 2026-07-18."
    )


def test_individual_rules_preserve_structured_values_while_numbers_stay_on(
    tmp_path: Path,
) -> None:
    normalizer = TextNormalizer(db_path=tmp_path / "normalization.sqlite3")
    source = (
        "Dr. Fox has 12, pays $20, gets 50%, carries 8 GB, ranks 3rd "
        "in XXI on 2026-07-18."
    )

    result = normalizer.normalize(
        source,
        language="en",
        rules={
            "enabled": True,
            "numbers": True,
            "ordinals": True,
            "dates": False,
            "currencies": False,
            "percentages": False,
            "measurements": False,
            "roman_numerals": True,
        },
    )

    assert result == (
        "Doctor Fox has twelve, pays $20, gets 50%, carries 8 GB, ranks third "
        "in twenty-one on 2026-07-18."
    )


def test_existing_english_database_is_migrated_without_losing_edits(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "old.sqlite3"
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE normalization_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE normalization_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                language TEXT NOT NULL,
                category TEXT NOT NULL,
                source TEXT NOT NULL,
                replacement TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                is_default INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                UNIQUE(language, source)
            );
            INSERT INTO normalization_meta VALUES ('initialized:en', '1');
            INSERT INTO normalization_entries
                (language, category, source, replacement, enabled, is_default, created_at, updated_at)
            VALUES ('en', 'custom', 'LTV', 'My custom pronunciation', 1, 0, 1, 1);
            """
        )

    store = TextNormalizationStore(db_path)

    assert store.list_entries("en")[0].replacement == "My custom pronunciation"
    assert store.dictionary("es") is not None
    assert store.list_entries("es")


def test_pipeline_applies_normalization_before_markup_compilation(tmp_path: Path) -> None:
    options = AudioGenerationOptions(
        output_dir=tmp_path,
        voice_config={"engine": "piper", "language": "en_US"},
        ffmpeg_path="ffmpeg",
        text_normalization_enabled=True,
        text_normalization_language="auto",
        text_normalization_db_path=tmp_path / "normalization.sqlite3",
    )
    logs: list[str] = []
    pipeline = AudioPipeline(_NoopEngine(), log_callback=logs.append)  # type: ignore[arg-type]

    groups = pipeline._prepare_groups("Mr. Fox {{pause 250}} has 2 GPUs.", options)

    assert [chunk.text for group in groups for chunk in group.chunks] == [
        "Mister Fox",
        "has two GPUs.",
    ]
    assert any("Text normalization applied" in message for message in logs)


def test_pipeline_uses_selected_automatic_rules(tmp_path: Path) -> None:
    options = AudioGenerationOptions(
        output_dir=tmp_path,
        voice_config={"engine": "piper", "language": "en_US"},
        ffmpeg_path="ffmpeg",
        text_normalization_enabled=True,
        text_normalization_language="auto",
        text_normalization_db_path=tmp_path / "normalization.sqlite3",
        text_normalization_rules={"enabled": False},
    )
    pipeline = AudioPipeline(_NoopEngine())  # type: ignore[arg-type]

    groups = pipeline._prepare_groups("Dr. Fox has 2 GPUs.", options)

    assert [chunk.text for group in groups for chunk in group.chunks] == [
        "Doctor Fox has 2 GPUs."
    ]
