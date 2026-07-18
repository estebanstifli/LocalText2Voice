from __future__ import annotations

import re
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from num2words import num2words

from app.utils.paths import app_data_root


DEFAULT_DICTIONARY_VERSION = 2
DEFAULT_ENGLISH_ENTRIES: tuple[tuple[str, str, str], ...] = (
    ("symbols", "&", "and"),
    ("symbols", "%", "percent"),
    ("symbols", "+", "plus"),
    ("symbols", "=", "equals"),
    ("symbols", "@", "at"),
    ("symbols", "€", "euros"),
    ("symbols", "$", "dollars"),
    ("symbols", "£", "pounds"),
    ("symbols", "°", "degrees"),
    ("abbreviations", "Mr.", "Mister"),
    ("abbreviations", "Mrs.", "Missus"),
    ("abbreviations", "Ms.", "Miz"),
    ("abbreviations", "Dr.", "Doctor"),
    ("abbreviations", "Prof.", "Professor"),
    ("abbreviations", "Sr.", "Senior"),
    ("abbreviations", "Jr.", "Junior"),
    ("abbreviations", "St.", "Saint"),
    ("abbreviations", "vs.", "versus"),
    ("abbreviations", "etc.", "et cetera"),
    ("abbreviations", "e.g.", "for example"),
    ("abbreviations", "i.e.", "that is"),
    ("abbreviations", "No.", "Number"),
    ("abbreviations", "approx.", "approximately"),
    ("time", "a.m.", "A M"),
    ("time", "p.m.", "P M"),
    ("time", "AM", "A M"),
    ("time", "PM", "P M"),
    ("units", "km", "kilometers"),
    ("units", "cm", "centimeters"),
    ("units", "mm", "millimeters"),
    ("units", "kg", "kilograms"),
    ("units", "mg", "milligrams"),
    ("units", "mph", "miles per hour"),
    ("units", "km/h", "kilometers per hour"),
    ("units", "Hz", "hertz"),
    ("units", "kHz", "kilohertz"),
    ("units", "MHz", "megahertz"),
    ("units", "GHz", "gigahertz"),
    ("units", "MB", "megabytes"),
    ("units", "GB", "gigabytes"),
    ("units", "TB", "terabytes"),
    ("units", "°C", "degrees Celsius"),
    ("units", "°F", "degrees Fahrenheit"),
    ("acronyms", "AI", "A I"),
    ("acronyms", "TTS", "T T S"),
    ("acronyms", "CPU", "C P U"),
    ("acronyms", "GPU", "G P U"),
    ("acronyms", "USB", "U S B"),
    ("acronyms", "HTML", "H T M L"),
    ("acronyms", "HTTP", "H T T P"),
    ("acronyms", "URL", "U R L"),
    ("acronyms", "USA", "U S A"),
    ("acronyms", "UK", "U K"),
    ("internet", "www.", "W W W dot "),
    ("internet", ".com", " dot com"),
    ("internet", ".org", " dot org"),
    ("internet", ".net", " dot net"),
)


def _flatten_dictionary(
    categories: Mapping[str, Mapping[str, str]],
) -> tuple[tuple[str, str, str], ...]:
    return tuple(
        (category, source, replacement)
        for category, values in categories.items()
        for source, replacement in values.items()
    )


BUILTIN_DICTIONARY_NAMES: dict[str, str] = {
    "ar": "العربية",
    "de": "Deutsch",
    "en": "English",
    "es": "Español",
    "fr": "Français",
    "hi": "हिन्दी",
    "it": "Italiano",
    "ja": "日本語",
    "pt": "Português",
    "zh": "中文",
}

DEFAULT_DICTIONARY_ENTRIES: dict[str, tuple[tuple[str, str, str], ...]] = {
    "en": DEFAULT_ENGLISH_ENTRIES,
    "ar": _flatten_dictionary(
        {
            "symbols": {
                "&": "و", "%": "بالمئة", "+": "زائد", "=": "يساوي",
                "@": "عند", "€": "يورو", "$": "دولار", "£": "جنيه",
                "°": "درجة",
            },
            "abbreviations": {
                "د.": "دكتور", "أ.": "أستاذ", "مثلاً": "على سبيل المثال",
            },
            "units": {
                "km": "كيلومترات", "cm": "سنتيمترات", "mm": "مليمترات",
                "kg": "كيلوغرامات", "mg": "مليغرامات", "km/h": "كيلومتر في الساعة",
                "Hz": "هرتز", "kHz": "كيلوهرتز", "MHz": "ميغاهرتز",
                "GHz": "غيغاهرتز", "MB": "ميغابايت", "GB": "غيغابايت",
                "TB": "تيرابايت", "°C": "درجة مئوية", "°F": "درجة فهرنهايت",
            },
            "internet": {"www.": "دبليو دبليو دبليو نقطة ", ".com": " نقطة كوم"},
        }
    ),
    "de": _flatten_dictionary(
        {
            "symbols": {
                "&": "und", "%": "Prozent", "+": "plus", "=": "gleich",
                "@": "at", "€": "Euro", "$": "Dollar", "£": "Pfund",
                "°": "Grad",
            },
            "abbreviations": {
                "Hr.": "Herr", "Fr.": "Frau", "Dr.": "Doktor",
                "Prof.": "Professor", "bzw.": "beziehungsweise",
                "z. B.": "zum Beispiel", "ca.": "circa",
            },
            "time": {"a.m.": "A M", "p.m.": "P M"},
            "units": {
                "km": "Kilometer", "cm": "Zentimeter", "mm": "Millimeter",
                "kg": "Kilogramm", "mg": "Milligramm", "km/h": "Kilometer pro Stunde",
                "Hz": "Hertz", "kHz": "Kilohertz", "MHz": "Megahertz",
                "GHz": "Gigahertz", "MB": "Megabyte", "GB": "Gigabyte",
                "TB": "Terabyte", "°C": "Grad Celsius", "°F": "Grad Fahrenheit",
            },
            "acronyms": {"KI": "K I", "TTS": "T T S", "USB": "U S B", "URL": "U R L"},
            "internet": {"www.": "W W W Punkt ", ".com": " Punkt com", ".de": " Punkt D E"},
        }
    ),
    "es": _flatten_dictionary(
        {
            "symbols": {
                "&": "y", "%": "por ciento", "+": "más", "=": "igual a",
                "@": "arroba", "€": "euros", "$": "dólares", "£": "libras",
                "°": "grados",
            },
            "abbreviations": {
                "Sr.": "Señor", "Sra.": "Señora", "Srta.": "Señorita",
                "Dr.": "Doctor", "Dra.": "Doctora", "Prof.": "Profesor",
                "etc.": "etcétera", "p. ej.": "por ejemplo", "aprox.": "aproximadamente",
            },
            "time": {"a. m.": "A M", "p. m.": "P M", "AM": "A M", "PM": "P M"},
            "units": {
                "km": "kilómetros", "cm": "centímetros", "mm": "milímetros",
                "kg": "kilogramos", "mg": "miligramos", "km/h": "kilómetros por hora",
                "Hz": "hercios", "kHz": "kilohercios", "MHz": "megahercios",
                "GHz": "gigahercios", "MB": "megabytes", "GB": "gigabytes",
                "TB": "terabytes", "°C": "grados Celsius", "°F": "grados Fahrenheit",
            },
            "acronyms": {"IA": "I A", "TTS": "T T S", "USB": "U S B", "URL": "U R L"},
            "internet": {"www.": "W W W punto ", ".com": " punto com", ".es": " punto es"},
        }
    ),
    "fr": _flatten_dictionary(
        {
            "symbols": {
                "&": "et", "%": "pour cent", "+": "plus", "=": "égale",
                "@": "arobase", "€": "euros", "$": "dollars", "£": "livres",
                "°": "degrés",
            },
            "abbreviations": {
                "M.": "Monsieur", "Mme": "Madame", "Mlle": "Mademoiselle",
                "Dr": "Docteur", "Pr": "Professeur", "etc.": "et cetera",
                "p. ex.": "par exemple", "env.": "environ",
            },
            "time": {"a.m.": "A M", "p.m.": "P M"},
            "units": {
                "km": "kilomètres", "cm": "centimètres", "mm": "millimètres",
                "kg": "kilogrammes", "mg": "milligrammes", "km/h": "kilomètres par heure",
                "Hz": "hertz", "kHz": "kilohertz", "MHz": "mégahertz",
                "GHz": "gigahertz", "MB": "mégaoctets", "GB": "gigaoctets",
                "TB": "téraoctets", "°C": "degrés Celsius", "°F": "degrés Fahrenheit",
            },
            "acronyms": {"IA": "I A", "TTS": "T T S", "USB": "U S B", "URL": "U R L"},
            "internet": {"www.": "W W W point ", ".com": " point com", ".fr": " point F R"},
        }
    ),
    "hi": _flatten_dictionary(
        {
            "symbols": {
                "&": "और", "%": "प्रतिशत", "+": "जोड़", "=": "बराबर",
                "@": "ऐट", "€": "यूरो", "$": "डॉलर", "£": "पाउंड", "°": "डिग्री",
            },
            "abbreviations": {"डॉ.": "डॉक्टर", "प्रो.": "प्रोफेसर", "आदि": "इत्यादि"},
            "units": {
                "km": "किलोमीटर", "cm": "सेंटीमीटर", "mm": "मिलीमीटर",
                "kg": "किलोग्राम", "mg": "मिलीग्राम", "km/h": "किलोमीटर प्रति घंटा",
                "Hz": "हर्ट्ज़", "kHz": "किलोहर्ट्ज़", "MHz": "मेगाहर्ट्ज़",
                "GHz": "गीगाहर्ट्ज़", "MB": "मेगाबाइट", "GB": "गीगाबाइट",
                "TB": "टेराबाइट", "°C": "डिग्री सेल्सियस", "°F": "डिग्री फ़ारेनहाइट",
            },
            "internet": {"www.": "डब्ल्यू डब्ल्यू डब्ल्यू डॉट ", ".com": " डॉट कॉम"},
        }
    ),
    "it": _flatten_dictionary(
        {
            "symbols": {
                "&": "e", "%": "percento", "+": "più", "=": "uguale",
                "@": "chiocciola", "€": "euro", "$": "dollari", "£": "sterline",
                "°": "gradi",
            },
            "abbreviations": {
                "Sig.": "Signor", "Sig.ra": "Signora", "Dott.": "Dottore",
                "Dott.ssa": "Dottoressa", "Prof.": "Professore", "ecc.": "eccetera",
                "ad es.": "ad esempio", "ca.": "circa",
            },
            "time": {"a.m.": "A M", "p.m.": "P M"},
            "units": {
                "km": "chilometri", "cm": "centimetri", "mm": "millimetri",
                "kg": "chilogrammi", "mg": "milligrammi", "km/h": "chilometri orari",
                "Hz": "hertz", "kHz": "kilohertz", "MHz": "megahertz",
                "GHz": "gigahertz", "MB": "megabyte", "GB": "gigabyte",
                "TB": "terabyte", "°C": "gradi Celsius", "°F": "gradi Fahrenheit",
            },
            "acronyms": {"IA": "I A", "TTS": "T T S", "USB": "U S B", "URL": "U R L"},
            "internet": {"www.": "W W W punto ", ".com": " punto com", ".it": " punto I T"},
        }
    ),
    "ja": _flatten_dictionary(
        {
            "symbols": {
                "&": "アンド", "%": "パーセント", "+": "プラス", "=": "イコール",
                "@": "アット", "€": "ユーロ", "$": "ドル", "£": "ポンド", "°": "度",
            },
            "units": {
                "km": "キロメートル", "cm": "センチメートル", "mm": "ミリメートル",
                "kg": "キログラム", "mg": "ミリグラム", "km/h": "キロメートル毎時",
                "Hz": "ヘルツ", "kHz": "キロヘルツ", "MHz": "メガヘルツ",
                "GHz": "ギガヘルツ", "MB": "メガバイト", "GB": "ギガバイト",
                "TB": "テラバイト", "°C": "度セルシウス", "°F": "度ファーレンハイト",
            },
            "acronyms": {"AI": "エー アイ", "TTS": "ティー ティー エス", "URL": "ユー アール エル"},
            "internet": {"www.": "ダブリュー ダブリュー ダブリュー ドット ", ".com": " ドット コム"},
        }
    ),
    "pt": _flatten_dictionary(
        {
            "symbols": {
                "&": "e", "%": "por cento", "+": "mais", "=": "igual a",
                "@": "arroba", "€": "euros", "$": "dólares", "£": "libras",
                "°": "graus",
            },
            "abbreviations": {
                "Sr.": "Senhor", "Sra.": "Senhora", "Dr.": "Doutor", "Dra.": "Doutora",
                "Prof.": "Professor", "etc.": "et cetera", "por ex.": "por exemplo",
                "aprox.": "aproximadamente",
            },
            "time": {"a.m.": "A M", "p.m.": "P M"},
            "units": {
                "km": "quilômetros", "cm": "centímetros", "mm": "milímetros",
                "kg": "quilogramas", "mg": "miligramas", "km/h": "quilômetros por hora",
                "Hz": "hertz", "kHz": "quilohertz", "MHz": "megahertz",
                "GHz": "gigahertz", "MB": "megabytes", "GB": "gigabytes",
                "TB": "terabytes", "°C": "graus Celsius", "°F": "graus Fahrenheit",
            },
            "acronyms": {"IA": "I A", "TTS": "T T S", "USB": "U S B", "URL": "U R L"},
            "internet": {"www.": "W W W ponto ", ".com": " ponto com", ".pt": " ponto P T"},
        }
    ),
    "zh": _flatten_dictionary(
        {
            "symbols": {
                "&": "和", "%": "百分之", "+": "加", "=": "等于",
                "@": "艾特", "€": "欧元", "$": "美元", "£": "英镑", "°": "度",
            },
            "units": {
                "km": "公里", "cm": "厘米", "mm": "毫米", "kg": "千克",
                "mg": "毫克", "km/h": "公里每小时", "Hz": "赫兹",
                "kHz": "千赫", "MHz": "兆赫", "GHz": "吉赫",
                "MB": "兆字节", "GB": "吉字节", "TB": "太字节",
                "°C": "摄氏度", "°F": "华氏度",
            },
            "acronyms": {"AI": "A I", "TTS": "T T S", "USB": "U S B", "URL": "U R L"},
            "internet": {"www.": "W W W 点 ", ".com": " 点 com", ".cn": " 点 C N"},
        }
    ),
}

NORMALIZATION_RULE_KEYS = (
    "numbers",
    "ordinals",
    "dates",
    "currencies",
    "percentages",
    "measurements",
    "roman_numerals",
)
DEFAULT_NORMALIZATION_RULES: dict[str, bool] = {
    "enabled": True,
    **{key: True for key in NORMALIZATION_RULE_KEYS},
}


def normalization_rule_settings(value: object = None) -> dict[str, bool]:
    raw = value if isinstance(value, Mapping) else {}
    return {
        key: bool(raw.get(key, default))
        for key, default in DEFAULT_NORMALIZATION_RULES.items()
    }


@dataclass(frozen=True)
class NormalizationEntry:
    id: int
    language: str
    category: str
    source: str
    replacement: str
    enabled: bool
    is_default: bool


@dataclass(frozen=True)
class NormalizationDictionary:
    language: str
    name: str
    is_builtin: bool


class TextNormalizationStore:
    """Editable language dictionaries backed by a small SQLite database."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or app_data_root() / "text_normalization.sqlite3"
        self._ensure_schema()

    def list_dictionaries(self) -> list[NormalizationDictionary]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT language, name, is_builtin FROM normalization_dictionaries "
                "ORDER BY is_builtin DESC, name COLLATE NOCASE, language COLLATE NOCASE"
            ).fetchall()
        return [self._row_to_dictionary(row) for row in rows]

    def dictionary(self, language: str) -> NormalizationDictionary | None:
        language = self._normalize_language_code(language)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT language, name, is_builtin FROM normalization_dictionaries "
                "WHERE language = ?",
                (language,),
            ).fetchone()
        return self._row_to_dictionary(row) if row is not None else None

    def create_dictionary(self, language: str, name: str) -> None:
        language = self._normalize_language_code(language)
        name = str(name).strip()
        if not name:
            raise ValueError("Dictionary name is required.")
        if len(name) > 100:
            raise ValueError("Dictionary name must be 100 characters or fewer.")
        now = int(time.time())
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO normalization_dictionaries "
                "(language, name, is_builtin, created_at, updated_at) "
                "VALUES (?, ?, 0, ?, ?)",
                (language, name, now, now),
            )

    def delete_dictionary(self, language: str) -> None:
        language = self._normalize_language_code(language)
        dictionary = self.dictionary(language)
        if dictionary is None:
            raise KeyError(f"Dictionary {language!r} does not exist.")
        if dictionary.is_builtin:
            raise ValueError("Built-in dictionaries can be reset but not deleted.")
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM normalization_entries WHERE language = ?", (language,)
            )
            connection.execute(
                "DELETE FROM normalization_dictionaries WHERE language = ?", (language,)
            )

    def export_dictionary(self, language: str) -> dict[str, Any]:
        dictionary = self.dictionary(language)
        if dictionary is None:
            raise KeyError(f"Dictionary {language!r} does not exist.")
        entries = self.list_entries(dictionary.language)
        return {
            "format": "localtext2voice-normalization-dictionary",
            "version": 1,
            "language": {
                "code": dictionary.language,
                "name": dictionary.name,
            },
            "entries": [
                {
                    "category": entry.category,
                    "source": entry.source,
                    "replacement": entry.replacement,
                    "enabled": entry.enabled,
                }
                for entry in entries
            ],
        }

    def import_dictionary(
        self,
        payload: Mapping[str, Any],
        *,
        mode: str = "merge",
    ) -> tuple[str, int]:
        if mode not in {"merge", "replace"}:
            raise ValueError("Import mode must be 'merge' or 'replace'.")
        language, name, entries = self._parse_import_payload(payload)
        existing = self.dictionary(language)
        now = int(time.time())
        with self._connect() as connection:
            if existing is None:
                connection.execute(
                    "INSERT INTO normalization_dictionaries "
                    "(language, name, is_builtin, created_at, updated_at) "
                    "VALUES (?, ?, 0, ?, ?)",
                    (language, name, now, now),
                )
            elif not existing.is_builtin and name != existing.name:
                connection.execute(
                    "UPDATE normalization_dictionaries SET name = ?, updated_at = ? "
                    "WHERE language = ?",
                    (name, now, language),
                )
            if mode == "replace":
                connection.execute(
                    "DELETE FROM normalization_entries WHERE language = ?",
                    (language,),
                )
            connection.executemany(
                """
                INSERT INTO normalization_entries (
                    language, category, source, replacement, enabled,
                    is_default, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 0, ?, ?)
                ON CONFLICT(language, source) DO UPDATE SET
                    category = excluded.category,
                    replacement = excluded.replacement,
                    enabled = excluded.enabled,
                    is_default = 0,
                    updated_at = excluded.updated_at
                """,
                [
                    (
                        language,
                        category,
                        source,
                        replacement,
                        int(enabled),
                        now,
                        now,
                    )
                    for category, source, replacement, enabled in entries
                ],
            )
        return language, len(entries)

    def inspect_import(self, payload: Mapping[str, Any]) -> tuple[str, str, int]:
        language, name, entries = self._parse_import_payload(payload)
        return language, name, len(entries)

    def list_entries(
        self,
        language: str,
        *,
        category: str = "",
        search: str = "",
        enabled_only: bool = False,
    ) -> list[NormalizationEntry]:
        clauses = ["language = ?"]
        values: list[object] = [language]
        if category:
            clauses.append("category = ?")
            values.append(category)
        if enabled_only:
            clauses.append("enabled = 1")
        if search.strip():
            clauses.append(
                "(source LIKE ? ESCAPE '\\' OR replacement LIKE ? ESCAPE '\\')"
            )
            escaped = self._escape_like(search.strip())
            values.extend((f"%{escaped}%", f"%{escaped}%"))
        query = (
            "SELECT id, language, category, source, replacement, enabled, is_default "
            "FROM normalization_entries WHERE "
            + " AND ".join(clauses)
            + " ORDER BY category COLLATE NOCASE, source COLLATE NOCASE, id"
        )
        with self._connect() as connection:
            rows = connection.execute(query, values).fetchall()
        return [self._row_to_entry(row) for row in rows]

    def categories(self, language: str) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT DISTINCT category FROM normalization_entries "
                "WHERE language = ? ORDER BY category COLLATE NOCASE",
                (language,),
            ).fetchall()
        return [str(row[0]) for row in rows]

    def add_entry(
        self,
        language: str,
        category: str,
        source: str,
        replacement: str,
        *,
        enabled: bool = True,
    ) -> int:
        language, category, source, replacement = self._validated_values(
            language, category, source, replacement
        )
        now = int(time.time())
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO normalization_entries (
                    language, category, source, replacement, enabled,
                    is_default, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (language, category, source, replacement, int(enabled), now, now),
            )
            return int(cursor.lastrowid)

    def update_entry(
        self,
        entry_id: int,
        *,
        category: str,
        source: str,
        replacement: str,
        enabled: bool,
    ) -> None:
        _language, category, source, replacement = self._validated_values(
            "placeholder", category, source, replacement
        )
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE normalization_entries
                SET category = ?, source = ?, replacement = ?, enabled = ?,
                    is_default = 0, updated_at = ?
                WHERE id = ?
                """,
                (
                    category,
                    source,
                    replacement,
                    int(enabled),
                    int(time.time()),
                    int(entry_id),
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Normalization entry {entry_id} does not exist.")

    def delete_entries(self, entry_ids: Sequence[int]) -> int:
        ids = sorted({int(entry_id) for entry_id in entry_ids})
        if not ids:
            return 0
        placeholders = ",".join("?" for _entry_id in ids)
        with self._connect() as connection:
            cursor = connection.execute(
                f"DELETE FROM normalization_entries WHERE id IN ({placeholders})",
                ids,
            )
            return int(cursor.rowcount)

    def reset_language(self, language: str) -> None:
        language = self._normalize_language_code(language)
        if language not in DEFAULT_DICTIONARY_ENTRIES:
            raise ValueError(f"No default dictionary is available for {language!r}.")
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM normalization_entries WHERE language = ?",
                (language,),
            )
            self._insert_defaults(connection, language)
            connection.execute(
                "INSERT INTO normalization_meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (f"defaults_version:{language}", str(DEFAULT_DICTIONARY_VERSION)),
            )

    def _ensure_schema(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS normalization_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS normalization_dictionaries (
                    language TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    is_builtin INTEGER NOT NULL DEFAULT 0 CHECK(is_builtin IN (0, 1)),
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS normalization_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    language TEXT NOT NULL,
                    category TEXT NOT NULL,
                    source TEXT NOT NULL,
                    replacement TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
                    is_default INTEGER NOT NULL DEFAULT 0 CHECK(is_default IN (0, 1)),
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    UNIQUE(language, source)
                );

                CREATE INDEX IF NOT EXISTS idx_normalization_language_category
                ON normalization_entries(language, category, enabled);
                """
            )
            now = int(time.time())
            connection.executemany(
                "INSERT INTO normalization_dictionaries "
                "(language, name, is_builtin, created_at, updated_at) "
                "VALUES (?, ?, 1, ?, ?) "
                "ON CONFLICT(language) DO UPDATE SET "
                "name = excluded.name, updated_at = excluded.updated_at "
                "WHERE normalization_dictionaries.is_builtin = 1",
                [
                    (language, name, now, now)
                    for language, name in BUILTIN_DICTIONARY_NAMES.items()
                ],
            )
            for language in BUILTIN_DICTIONARY_NAMES:
                initialized = connection.execute(
                    "SELECT value FROM normalization_meta WHERE key = ?",
                    (f"initialized:{language}",),
                ).fetchone()
                if initialized is not None:
                    continue
                self._insert_defaults(connection, language)
                connection.execute(
                    "INSERT INTO normalization_meta (key, value) VALUES (?, ?)",
                    (f"initialized:{language}", "1"),
                )
            connection.execute(
                "INSERT INTO normalization_meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                ("schema_version", str(DEFAULT_DICTIONARY_VERSION)),
            )

    @staticmethod
    def _insert_defaults(connection: sqlite3.Connection, language: str) -> None:
        entries = DEFAULT_DICTIONARY_ENTRIES.get(language)
        if entries is None:
            return
        now = int(time.time())
        connection.executemany(
            """
            INSERT OR IGNORE INTO normalization_entries (
                language, category, source, replacement, enabled,
                is_default, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 1, 1, ?, ?)
            """,
            [
                (language, category, source, replacement, now, now)
                for category, source, replacement in entries
            ],
        )

    @classmethod
    def _parse_import_payload(
        cls,
        payload: Mapping[str, Any],
    ) -> tuple[str, str, list[tuple[str, str, str, bool]]]:
        if not isinstance(payload, Mapping):
            raise ValueError("The JSON root must be an object.")
        format_name = payload.get("format")
        if format_name not in {None, "localtext2voice-normalization-dictionary"}:
            raise ValueError("This is not a LocalText2Voice normalization dictionary.")
        language_value = payload.get("language")
        if isinstance(language_value, Mapping):
            language = language_value.get("code", "")
            name = language_value.get("name", "")
        else:
            language = language_value
            name = payload.get("name", "")
        language = cls._normalize_language_code(str(language or ""))
        name = str(name or BUILTIN_DICTIONARY_NAMES.get(language, language)).strip()
        if not name:
            raise ValueError("The dictionary language name is required.")

        raw_entries = payload.get("entries")
        normalized: list[tuple[str, str, str, bool]] = []
        if isinstance(raw_entries, list):
            for index, raw_entry in enumerate(raw_entries, start=1):
                if not isinstance(raw_entry, Mapping):
                    raise ValueError(f"Entry {index} must be an object.")
                try:
                    _unused, category, source, replacement = cls._validated_values(
                        language,
                        str(raw_entry.get("category", "")),
                        str(raw_entry.get("source", "")),
                        str(raw_entry.get("replacement", "")),
                    )
                except ValueError as exc:
                    raise ValueError(f"Entry {index}: {exc}") from exc
                normalized.append(
                    (category, source, replacement, bool(raw_entry.get("enabled", True)))
                )
        elif raw_entries is not None:
            raise ValueError("The 'entries' value must be a list.")
        else:
            reserved = {"format", "version", "language", "name", "number_rules"}
            for category, values in payload.items():
                if category in reserved:
                    continue
                if not isinstance(values, Mapping):
                    raise ValueError(
                        "Legacy dictionaries must contain category objects of find/replace pairs."
                    )
                for source, replacement in values.items():
                    _unused, valid_category, valid_source, valid_replacement = (
                        cls._validated_values(
                            language,
                            str(category),
                            str(source),
                            str(replacement),
                        )
                    )
                    normalized.append(
                        (valid_category, valid_source, valid_replacement, True)
                    )
        if not normalized and not isinstance(raw_entries, list):
            raise ValueError("The dictionary does not contain any entries.")
        if len(normalized) > 10_000:
            raise ValueError("A dictionary can contain at most 10,000 entries.")
        seen: set[str] = set()
        for _category, source, _replacement, _enabled in normalized:
            if source in seen:
                raise ValueError(f"Duplicate source value in dictionary: {source!r}.")
            seen.add(source)
        return language, name, normalized

    @staticmethod
    def _validated_values(
        language: str,
        category: str,
        source: str,
        replacement: str,
    ) -> tuple[str, str, str, str]:
        values = tuple(str(value).strip() for value in (language, category, source))
        language, category, source = values
        replacement = str(replacement).strip()
        if not language or not category or not source or not replacement:
            raise ValueError("Language, category, source, and replacement are required.")
        return language, category, source, replacement

    @staticmethod
    def _normalize_language_code(language: str) -> str:
        value = str(language).strip().casefold().replace("_", "-")
        if value == "auto":
            raise ValueError("'auto' is reserved and cannot be a dictionary code.")
        if not re.fullmatch(r"[a-z]{2,8}(?:-[a-z0-9]{1,8})*", value):
            raise ValueError(
                "Use a language code such as en, es, pt-br, or your own short locale code."
            )
        return value

    @staticmethod
    def _escape_like(value: str) -> str:
        return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> NormalizationEntry:
        return NormalizationEntry(
            id=int(row["id"]),
            language=str(row["language"]),
            category=str(row["category"]),
            source=str(row["source"]),
            replacement=str(row["replacement"]),
            enabled=bool(row["enabled"]),
            is_default=bool(row["is_default"]),
        )

    @staticmethod
    def _row_to_dictionary(row: sqlite3.Row) -> NormalizationDictionary:
        return NormalizationDictionary(
            language=str(row["language"]),
            name=str(row["name"]),
            is_builtin=bool(row["is_builtin"]),
        )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(str(self.db_path))
        try:
            connection.row_factory = sqlite3.Row
            yield connection
            connection.commit()
        finally:
            connection.close()


_SMALL = (
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
)
_TENS = (
    "",
    "",
    "twenty",
    "thirty",
    "forty",
    "fifty",
    "sixty",
    "seventy",
    "eighty",
    "ninety",
)
_SCALES = (
    (1_000_000_000_000, "trillion"),
    (1_000_000_000, "billion"),
    (1_000_000, "million"),
    (1_000, "thousand"),
)
_ORDINAL_WORDS = {
    "one": "first",
    "two": "second",
    "three": "third",
    "four": "fourth",
    "five": "fifth",
    "six": "sixth",
    "seven": "seventh",
    "eight": "eighth",
    "nine": "ninth",
    "ten": "tenth",
    "eleven": "eleventh",
    "twelve": "twelfth",
    "thirteen": "thirteenth",
    "fourteen": "fourteenth",
    "fifteen": "fifteenth",
    "sixteen": "sixteenth",
    "seventeen": "seventeenth",
    "eighteen": "eighteenth",
    "nineteen": "nineteenth",
    "twenty": "twentieth",
    "thirty": "thirtieth",
    "forty": "fortieth",
    "fifty": "fiftieth",
    "sixty": "sixtieth",
    "seventy": "seventieth",
    "eighty": "eightieth",
    "ninety": "ninetieth",
    "hundred": "hundredth",
    "thousand": "thousandth",
    "million": "millionth",
    "billion": "billionth",
    "trillion": "trillionth",
}
_MONTHS = (
    "",
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def english_integer_to_words(value: int) -> str:
    if value < 0:
        return f"negative {english_integer_to_words(-value)}"
    if value < 20:
        return _SMALL[value]
    if value < 100:
        tens, remainder = divmod(value, 10)
        return _TENS[tens] if remainder == 0 else f"{_TENS[tens]}-{_SMALL[remainder]}"
    if value < 1_000:
        hundreds, remainder = divmod(value, 100)
        result = f"{_SMALL[hundreds]} hundred"
        return result if remainder == 0 else f"{result} {english_integer_to_words(remainder)}"
    for scale, label in _SCALES:
        if value >= scale:
            leading, remainder = divmod(value, scale)
            result = f"{english_integer_to_words(leading)} {label}"
            return result if remainder == 0 else f"{result} {english_integer_to_words(remainder)}"
    return str(value)


def english_number_to_words(value: str) -> str:
    normalized = value.replace(",", "").strip()
    if not re.fullmatch(r"[-+]?\d+(?:\.\d+)?", normalized):
        return value
    sign = ""
    if normalized.startswith(("-", "+")):
        sign = "negative " if normalized[0] == "-" else "plus "
        normalized = normalized[1:]
    if "." in normalized:
        integer, decimal = normalized.split(".", 1)
        integer_words = english_integer_to_words(int(integer or "0"))
        decimal_words = " ".join(_SMALL[int(character)] for character in decimal)
        return f"{sign}{integer_words} point {decimal_words}"
    return f"{sign}{english_integer_to_words(int(normalized or '0'))}"


def english_ordinal_to_words(value: int) -> str:
    cardinal = english_integer_to_words(value)
    prefix, separator, final = cardinal.rpartition(" ")
    if "-" in final:
        tens, hyphen, last = final.rpartition("-")
        ordinal_final = f"{tens}{hyphen}{_ORDINAL_WORDS.get(last, last + 'th')}"
    else:
        ordinal_final = _ORDINAL_WORDS.get(final, f"{final}th")
    return f"{prefix}{separator}{ordinal_final}" if prefix else ordinal_final


NUMBER_RULE_LANGUAGES = frozenset({"ar", "de", "en", "es", "fr", "it", "ja", "pt"})
_COMMA_DECIMAL_LANGUAGES = frozenset({"de", "es", "fr", "it", "pt"})
_DECIMAL_WORDS = {
    "ar": "فاصلة",
    "de": "Komma",
    "en": "point",
    "es": "punto",
    "fr": "virgule",
    "it": "virgola",
    "ja": "点",
    "pt": "vírgula",
}
_SIGN_WORDS = {
    "ar": {"-": "سالب", "+": "موجب"},
    "de": {"-": "minus", "+": "plus"},
    "en": {"-": "negative", "+": "plus"},
    "es": {"-": "menos", "+": "más"},
    "fr": {"-": "moins", "+": "plus"},
    "it": {"-": "meno", "+": "più"},
    "ja": {"-": "マイナス", "+": "プラス"},
    "pt": {"-": "menos", "+": "mais"},
}
_PERCENT_WORDS = {
    "ar": "بالمئة",
    "de": "Prozent",
    "en": "percent",
    "es": "por ciento",
    "fr": "pour cent",
    "it": "percento",
    "ja": "パーセント",
    "pt": "por cento",
}
_LANGUAGE_ALIASES = {
    "arabic": "ar", "العربية": "ar",
    "german": "de", "deutsch": "de",
    "english": "en",
    "spanish": "es", "español": "es", "espanol": "es",
    "french": "fr", "français": "fr", "francais": "fr",
    "hindi": "hi", "हिन्दी": "hi", "हिंदी": "hi",
    "italian": "it", "italiano": "it",
    "japanese": "ja", "日本語": "ja",
    "portuguese": "pt", "português": "pt", "portugues": "pt",
    "chinese": "zh", "中文": "zh",
}
_CURRENCY_CODES = {"$": "USD", "€": "EUR", "£": "GBP"}
_NUM2WORDS_CURRENCY_LANGUAGES = frozenset({"de", "es", "fr", "it", "pt"})


def number_rules_available(language: str) -> bool:
    return str(language).casefold().replace("_", "-").split("-", 1)[0] in NUMBER_RULE_LANGUAGES


def _canonical_decimal(value: str, language: str) -> tuple[str, str, str]:
    """Return sign, integer digits, and fractional digits for a localized number."""
    token = str(value).strip().replace(" ", "")
    sign = token[0] if token[:1] in {"-", "+"} else ""
    if sign:
        token = token[1:]
    base = language.split("-", 1)[0]
    comma_decimal = base in _COMMA_DECIMAL_LANGUAGES
    if "," in token and "." in token:
        if comma_decimal:
            integer_part, decimal_part = token.rsplit(",", 1)
            if "," in integer_part or not decimal_part:
                raise ValueError(f"Invalid number: {value!r}")
            groups = integer_part.split(".")
            if len(groups) > 1 and any(len(group) != 3 for group in groups[1:]):
                raise ValueError(f"Invalid number: {value!r}")
            token = token.replace(".", "").replace(",", ".")
        else:
            integer_part, decimal_part = token.rsplit(".", 1)
            groups = integer_part.split(",")
            if "." in integer_part or not decimal_part or any(
                len(group) != 3 for group in groups[1:]
            ):
                raise ValueError(f"Invalid number: {value!r}")
            token = token.replace(",", "")
    elif "," in token:
        if comma_decimal:
            pieces = token.split(",")
            if len(pieces) != 2:
                raise ValueError(f"Invalid number: {value!r}")
            token = "".join(pieces[:-1]) + "." + pieces[-1]
        else:
            pieces = token.split(",")
            if len(pieces) > 2 and any(len(piece) != 3 for piece in pieces[1:]):
                raise ValueError(f"Invalid number: {value!r}")
            token = token.replace(",", "")
    elif "." in token and comma_decimal:
        pieces = token.split(".")
        if len(pieces) > 2:
            if any(len(piece) != 3 for piece in pieces[1:]):
                raise ValueError(f"Invalid number: {value!r}")
            token = "".join(pieces)
        elif len(pieces) == 2 and len(pieces[-1]) == 3:
            token = "".join(pieces)
    try:
        Decimal(token)
    except InvalidOperation as exc:
        raise ValueError(f"Invalid number: {value!r}") from exc
    integer, dot, fraction = token.partition(".")
    return sign, integer or "0", fraction if dot else ""


def multilingual_number_to_words(value: str, language: str) -> str:
    base = str(language).casefold().replace("_", "-").split("-", 1)[0]
    if base not in NUMBER_RULE_LANGUAGES:
        return value
    if base == "en":
        return english_number_to_words(value)
    try:
        sign, integer, fraction = _canonical_decimal(value, base)
        integer_words = str(num2words(int(integer), lang=base))
        sign_word = _SIGN_WORDS[base].get(sign, "")
        joiner = "" if base == "ja" else " "
        prefix = f"{sign_word}{joiner}" if sign_word else ""
        if not fraction:
            return f"{prefix}{integer_words}"
        digit_words = joiner.join(str(num2words(int(digit), lang=base)) for digit in fraction)
        return joiner.join(
            part for part in (prefix.rstrip(), integer_words, _DECIMAL_WORDS[base], digit_words) if part
        )
    except (InvalidOperation, NotImplementedError, OverflowError, ValueError):
        return value


def multilingual_ordinal_to_words(value: int, language: str) -> str:
    base = str(language).casefold().replace("_", "-").split("-", 1)[0]
    if base == "en":
        return english_ordinal_to_words(value)
    if base not in NUMBER_RULE_LANGUAGES:
        return str(value)
    try:
        return str(num2words(value, ordinal=True, lang=base))
    except (NotImplementedError, OverflowError, ValueError):
        return str(value)


class TextNormalizer:
    """Apply editable dictionaries and conservative pronunciation rules."""

    _markup_command = re.compile(r"(\{\{.*?\}\})", re.DOTALL)
    _percent = re.compile(r"(?<!\w)([-+]?\d[\d,]*(?:\.\d+)?)\s*%")
    _currency = re.compile(
        r"(?<!\w)(?P<sign>[-+]?)\s*(?P<symbol>[$€£])\s*"
        r"(?P<amount>\d[\d.,]*\d|\d)(?!\w)"
    )
    _currency_suffix = re.compile(
        r"(?<![\w.])(?P<sign>[-+]?)\s*"
        r"(?P<amount>\d[\d.,]*\d|\d)\s*(?P<symbol>[$€£])(?!\w)"
    )
    _iso_date = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
    _numeric_date = re.compile(
        r"\b(?P<first>\d{1,2})[./-](?P<second>\d{1,2})[./-]"
        r"(?P<year>\d{2}|\d{4})\b"
    )
    _dotted_sequence = re.compile(r"(?<![\w.])\d+(?:\.\d+){2,}(?!\d)")
    _ordinal = re.compile(r"\b(\d+)(?:st|nd|rd|th)\b", re.IGNORECASE)
    _roman = re.compile(r"\b[IVXLCDM]{2,}\b")
    _number = re.compile(r"(?<![\w.])[-+]?(?:\d[\d.,]*\d|\d)(?!\w)")
    _cjk_number = re.compile(r"(?<![\d.])[-+]?(?:\d[\d.,]*\d|\d)(?![\d.])")
    _horizontal_space = re.compile(r"[^\S\n]+")

    def __init__(
        self,
        store: TextNormalizationStore | None = None,
        db_path: Path | None = None,
    ) -> None:
        self.store = store or TextNormalizationStore(db_path)

    @staticmethod
    def resolve_language(selected: str, language_hint: str = "") -> str | None:
        selected_token = str(selected or "auto").strip().casefold().replace("_", "-")
        if selected_token not in {"", "auto"}:
            return TextNormalizer._language_token(selected_token)
        hint = str(language_hint).strip().casefold().replace("_", "-")
        direct = TextNormalizer._language_token(hint)
        if direct:
            return direct
        for alias, code in _LANGUAGE_ALIASES.items():
            if re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", hint):
                return code
        return None

    @staticmethod
    def _language_token(value: str) -> str | None:
        token = str(value).strip().casefold().replace("_", "-")
        if not token:
            return None
        if token in _LANGUAGE_ALIASES:
            return _LANGUAGE_ALIASES[token]
        first = re.split(r"[\s(/]", token, maxsplit=1)[0]
        if first in _LANGUAGE_ALIASES:
            return _LANGUAGE_ALIASES[first]
        if re.fullmatch(r"[a-z]{2,8}(?:-[a-z0-9]{1,8})*", first):
            return first
        return None

    def normalize(
        self,
        text: str,
        *,
        language: str = "auto",
        language_hint: str = "",
        preserve_markup: bool = True,
        rules: object = None,
    ) -> str:
        resolved = self.resolve_language(language, language_hint)
        if resolved is None or not text:
            return text
        dictionary = self.store.dictionary(resolved)
        if dictionary is None and "-" in resolved:
            dictionary = self.store.dictionary(resolved.split("-", 1)[0])
        if dictionary is None:
            return text
        resolved = dictionary.language
        entries = self.store.list_entries(resolved, enabled_only=True)
        rule_settings = normalization_rule_settings(rules)
        if not preserve_markup:
            return self._normalize_piece(text, entries, resolved, rule_settings)
        parts = self._markup_command.split(text)
        return "".join(
            part
            if self._markup_command.fullmatch(part)
            else self._normalize_piece(part, entries, resolved, rule_settings)
            for part in parts
        )

    def _normalize_piece(
        self,
        text: str,
        entries: Sequence[NormalizationEntry],
        language: str,
        rules: Mapping[str, bool],
    ) -> str:
        text, protected = self._protect_disabled_rules(
            text, entries, language, rules
        )
        if language.split("-", 1)[0] == "en":
            return self._normalize_english_piece(text, entries, rules, protected)
        automatic_numbers = number_rules_available(language)
        if automatic_numbers and self._rule_active(rules, "dates"):
            text = self._iso_date.sub(
                lambda match: self._replace_generic_iso_date(match, language), text
            )
            text = self._numeric_date.sub(
                lambda match: self._replace_generic_numeric_date(match, language), text
            )
            text = self._dotted_sequence.sub(
                lambda match: self._replace_dotted_sequence(match, language), text
            )
        if automatic_numbers and self._rule_active(rules, "currencies"):
            text = self._currency.sub(
                lambda match: self._replace_multilingual_currency(match, language, entries),
                text,
            )
            text = self._currency_suffix.sub(
                lambda match: self._replace_multilingual_currency(match, language, entries),
                text,
            )
        if automatic_numbers and self._rule_active(rules, "percentages"):
            text = self._percent.sub(
                lambda match: (
                    f"{multilingual_number_to_words(match.group(1), language)} "
                    f"{_PERCENT_WORDS[language.split('-', 1)[0]]}"
                ),
                text,
            )
        if self._rule_active(rules, "measurements"):
            text = self._replace_measurements(text, entries, language)
        if automatic_numbers and self._rule_active(rules, "ordinals"):
            text = self._replace_multilingual_ordinals(text, language)
        if automatic_numbers and self._rule_active(rules, "roman_numerals"):
            text = self._roman.sub(
                lambda match: self._replace_roman(match, language), text
            )
        if automatic_numbers and self._rule_active(rules, "numbers"):
            number_pattern = self._cjk_number if language.split("-", 1)[0] == "ja" else self._number
            text = number_pattern.sub(
                lambda match: multilingual_number_to_words(match.group(0), language),
                text,
            )
        text = self._apply_dictionary(text, entries, rules)
        return self._restore_protected(
            self._horizontal_space.sub(" ", text), protected
        )

    def _normalize_english_piece(
        self,
        text: str,
        entries: Sequence[NormalizationEntry],
        rules: Mapping[str, bool],
        protected: Sequence[tuple[str, str]],
    ) -> str:
        if self._rule_active(rules, "dates"):
            text = self._iso_date.sub(self._replace_iso_date, text)
            text = self._numeric_date.sub(self._replace_numeric_date, text)
            text = self._dotted_sequence.sub(
                lambda match: self._replace_dotted_sequence(match, "en"), text
            )
        if self._rule_active(rules, "currencies"):
            text = self._currency.sub(self._replace_currency, text)
            text = self._currency_suffix.sub(self._replace_currency, text)
        if self._rule_active(rules, "percentages"):
            text = self._percent.sub(
                lambda match: f"{english_number_to_words(match.group(1))} percent",
                text,
            )
        if self._rule_active(rules, "measurements"):
            text = self._replace_measurements(text, entries)
        if self._rule_active(rules, "ordinals"):
            text = self._ordinal.sub(
                lambda match: english_ordinal_to_words(int(match.group(1))), text
            )
        if self._rule_active(rules, "roman_numerals"):
            text = self._roman.sub(self._replace_roman, text)
        if self._rule_active(rules, "numbers"):
            text = self._number.sub(
                lambda match: english_number_to_words(match.group(0)), text
            )
        text = self._apply_dictionary(text, entries, rules)
        return self._restore_protected(
            self._horizontal_space.sub(" ", text), protected
        )

    @staticmethod
    def _replace_iso_date(match: re.Match[str]) -> str:
        year, month, day = (int(value) for value in match.groups())
        try:
            date(year, month, day)
        except ValueError:
            return match.group(0)
        return (
            f"{_MONTHS[month]} {english_ordinal_to_words(day)}, "
            f"{english_integer_to_words(year)}"
        )

    @staticmethod
    def _replace_numeric_date(match: re.Match[str]) -> str:
        first, second, year = (int(value) for value in match.groups())
        # English numeric dates are normally month/day/year. Requiring a valid
        # month and day avoids treating most dotted version numbers as dates.
        try:
            date(year if year >= 100 else 2000 + year, first, second)
        except ValueError:
            return match.group(0)
        return (
            f"{english_integer_to_words(first)}/"
            f"{english_integer_to_words(second)}/"
            f"{english_integer_to_words(year)}"
        )

    @staticmethod
    def _replace_generic_iso_date(match: re.Match[str], language: str) -> str:
        year, month, day = (int(value) for value in match.groups())
        try:
            date(year, month, day)
        except ValueError:
            return match.group(0)
        return "/".join(
            multilingual_number_to_words(str(value), language)
            for value in (year, month, day)
        )

    @staticmethod
    def _replace_generic_numeric_date(match: re.Match[str], language: str) -> str:
        first, second, year = (int(value) for value in match.groups())
        full_year = year if year >= 100 else 2000 + year
        try:
            date(full_year, second, first)
        except ValueError:
            return match.group(0)
        return "/".join(
            multilingual_number_to_words(str(value), language)
            for value in (first, second, year)
        )

    @staticmethod
    def _replace_dotted_sequence(match: re.Match[str], language: str) -> str:
        return "/".join(
            multilingual_number_to_words(value, language)
            for value in match.group(0).split(".")
        )

    @staticmethod
    def _replace_currency(match: re.Match[str]) -> str:
        sign = "negative " if match.group("sign") == "-" else (
            "plus " if match.group("sign") == "+" else ""
        )
        symbol = match.group("symbol")
        amount = match.group("amount").replace(",", "")
        major_text, dot, minor_text = amount.partition(".")
        major = int(major_text)
        minor = int((minor_text + "00")[:2]) if dot else 0
        names = {
            "$": ("dollar", "cent"),
            "€": ("euro", "cent"),
            "£": ("pound", "penny"),
        }
        major_name, minor_name = names[symbol]
        phrase = (
            f"{english_integer_to_words(major)} {major_name if major == 1 else major_name + 's'}"
        )
        if minor:
            minor_plural = "pence" if symbol == "£" and minor != 1 else (
                minor_name if minor == 1 else minor_name + "s"
            )
            phrase += f" and {english_integer_to_words(minor)} {minor_plural}"
        return f"{sign}{phrase}"

    @staticmethod
    def _replace_multilingual_currency(
        match: re.Match[str],
        language: str,
        entries: Sequence[NormalizationEntry],
    ) -> str:
        base = language.split("-", 1)[0]
        raw_amount = match.group("amount")
        sign = match.group("sign")
        try:
            _embedded_sign, integer, fraction = _canonical_decimal(raw_amount, base)
            amount = Decimal(f"{integer}.{fraction}" if fraction else integer)
            if base in _NUM2WORDS_CURRENCY_LANGUAGES:
                phrase = str(
                    num2words(
                        amount,
                        lang=base,
                        to="currency",
                        currency=_CURRENCY_CODES[match.group("symbol")],
                    )
                )
            else:
                currency_name = next(
                    (
                        entry.replacement
                        for entry in entries
                        if entry.category.casefold() == "symbols"
                        and entry.source == match.group("symbol")
                    ),
                    match.group("symbol"),
                )
                phrase = f"{multilingual_number_to_words(raw_amount, language)} {currency_name}"
            sign_word = _SIGN_WORDS[base].get(sign, "")
            joiner = "" if base == "ja" else " "
            return f"{sign_word}{joiner if sign_word else ''}{phrase}"
        except (InvalidOperation, NotImplementedError, OverflowError, ValueError):
            return match.group(0)

    @staticmethod
    def _rule_active(rules: Mapping[str, bool], key: str) -> bool:
        return bool(rules.get("enabled", True) and rules.get(key, True))

    @classmethod
    def _protect_disabled_rules(
        cls,
        text: str,
        entries: Sequence[NormalizationEntry],
        language: str,
        rules: Mapping[str, bool],
    ) -> tuple[str, list[tuple[str, str]]]:
        protected: list[tuple[str, str]] = []

        def protect(pattern: re.Pattern[str], value: str) -> str:
            def replace(match: re.Match[str]) -> str:
                index = len(protected)
                if index >= 5_800:
                    return match.group(0)
                placeholder = f"\ue000{chr(0xE100 + index)}\ue001"
                protected.append((placeholder, match.group(0)))
                return placeholder

            return pattern.sub(replace, value)

        if not cls._rule_active(rules, "dates"):
            for pattern in (cls._iso_date, cls._numeric_date, cls._dotted_sequence):
                text = protect(pattern, text)
        if not cls._rule_active(rules, "currencies"):
            for pattern in (cls._currency, cls._currency_suffix):
                text = protect(pattern, text)
        if not cls._rule_active(rules, "percentages"):
            text = protect(cls._percent, text)
        if not cls._rule_active(rules, "measurements"):
            units = (
                entry
                for entry in entries
                if entry.category.casefold() == "units"
            )
            for entry in sorted(units, key=lambda item: len(item.source), reverse=True):
                text = protect(cls._measurement_pattern(entry.source), text)
        if not cls._rule_active(rules, "ordinals"):
            patterns = (
                (cls._ordinal,)
                if language.split("-", 1)[0] == "en"
                else cls._multilingual_ordinal_patterns(language)
            )
            for pattern in patterns:
                text = protect(pattern, text)
        return text, protected

    @staticmethod
    def _restore_protected(
        text: str,
        protected: Sequence[tuple[str, str]],
    ) -> str:
        for placeholder, original in protected:
            text = text.replace(placeholder, original)
        return text

    @staticmethod
    def _measurement_pattern(source: str) -> re.Pattern[str]:
        return re.compile(
            rf"(?<![\d.])(?P<number>[-+]?\d[\d,]*(?:\.\d+)?)\s*"
            rf"{re.escape(source)}(?![A-Za-z0-9_])"
        )

    @classmethod
    def _replace_measurements(
        cls,
        text: str,
        entries: Sequence[NormalizationEntry],
        language: str = "en",
    ) -> str:
        units = [entry for entry in entries if entry.category.casefold() == "units"]
        for entry in sorted(units, key=lambda item: len(item.source), reverse=True):
            pattern = cls._measurement_pattern(entry.source)

            def replace(match: re.Match[str], replacement: str = entry.replacement) -> str:
                raw_number = match.group("number")
                base = language.split("-", 1)[0]
                phrase = (
                    cls._singular_unit(replacement)
                    if base == "en" and cls._is_one(raw_number)
                    else replacement
                )
                spoken_number = (
                    english_number_to_words(raw_number)
                    if base == "en"
                    else multilingual_number_to_words(raw_number, language)
                )
                return f"{spoken_number} {phrase}"

            text = pattern.sub(replace, text)
        return text

    @staticmethod
    def _is_one(value: str) -> bool:
        try:
            return float(value.replace(",", "")) == 1.0
        except ValueError:
            return False

    @staticmethod
    def _singular_unit(value: str) -> str:
        if value.startswith("degrees "):
            return "degree " + value[len("degrees ") :]
        if value == "miles per hour":
            return "mile per hour"
        if value.endswith("bytes"):
            return value[:-1]
        if value.endswith("meters") or value.endswith("grams"):
            return value[:-1]
        return value

    @staticmethod
    def _replace_roman(match: re.Match[str], language: str = "en") -> str:
        token = match.group(0)
        value = TextNormalizer._roman_to_integer(token)
        return (
            multilingual_number_to_words(str(value), language)
            if value is not None
            else token
        )

    @staticmethod
    def _replace_multilingual_ordinals(text: str, language: str) -> str:
        for pattern in TextNormalizer._multilingual_ordinal_patterns(language):
            text = pattern.sub(
                lambda match: multilingual_ordinal_to_words(
                    int(match.group(1)), language
                ),
                text,
            )
        return text

    @staticmethod
    def _multilingual_ordinal_patterns(
        language: str,
    ) -> tuple[re.Pattern[str], ...]:
        base = language.split("-", 1)[0]
        if base in {"es", "it", "pt"}:
            return (re.compile(r"\b(\d+)\s*\.?\s*[ºª°](?!\w)"),)
        if base == "fr":
            return (
                re.compile(r"\b(\d+)(?:er|re|e|ème)\b", re.IGNORECASE),
            )
        if base == "ja":
            return (re.compile(r"第\s*(\d+)"),)
        return ()

    @staticmethod
    def _roman_to_integer(token: str) -> int | None:
        values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
        total = 0
        previous = 0
        for character in reversed(token):
            value = values[character]
            total += -value if value < previous else value
            previous = max(previous, value)
        if not 1 <= total <= 3999:
            return None
        canonical = TextNormalizer._integer_to_roman(total)
        return total if canonical == token else None

    @staticmethod
    def _integer_to_roman(value: int) -> str:
        numerals = (
            (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
            (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
            (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
        )
        result: list[str] = []
        for number, numeral in numerals:
            count, value = divmod(value, number)
            result.append(numeral * count)
        return "".join(result)

    @staticmethod
    def _apply_dictionary(
        text: str,
        entries: Sequence[NormalizationEntry],
        rules: Mapping[str, bool],
    ) -> str:
        for entry in sorted(entries, key=lambda item: len(item.source), reverse=True):
            category = entry.category.casefold()
            if category == "units":
                continue
            if category == "symbols":
                if (
                    entry.source == "%"
                    and not TextNormalizer._rule_active(rules, "percentages")
                ):
                    continue
                if (
                    entry.source in _CURRENCY_CODES
                    and not TextNormalizer._rule_active(rules, "currencies")
                ):
                    continue
                if (
                    entry.source == "°"
                    and not TextNormalizer._rule_active(rules, "measurements")
                ):
                    continue
                text = text.replace(
                    entry.source,
                    f" {entry.replacement.strip()} ",
                )
                continue
            if category == "internet":
                text = text.replace(entry.source, entry.replacement)
                continue
            flags = 0 if category == "acronyms" else re.IGNORECASE
            boundaries = (
                (r"(?<![A-Za-z0-9_])", r"(?![A-Za-z0-9_])")
                if category == "acronyms"
                else (r"(?<!\w)", r"(?!\w)")
            )
            pattern = re.compile(
                rf"{boundaries[0]}{re.escape(entry.source)}{boundaries[1]}", flags
            )
            text = pattern.sub(lambda _match, value=entry.replacement: value, text)
        return text
