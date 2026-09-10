"""Small, deterministic visual locks. The UI keeps editable plain-text fields.

The LLM still understands the story; this layer must never design a character.
Known appearance is sticky, source-backed revelations can correct inferred
choices, and equivalent clauses are not accumulated forever. Metadata is only
reused while its text snapshot matches (manual edits always take precedence).
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any


def _fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c))


# These describe an irrelevant absence, not an actual visible aid/disability.
# In particular, do not match "without glasses" or "no longer uses a wheelchair".
_DEFAULT_MOBILITY = re.compile(
    r"\b(?:with all limbs unobstructed(?: and able to (?:walk|move)(?: and (?:walk|move))? unaided)?|"
    r"(?:walking |walks? |moves? |moving |able to walk )?(?:without|no) mobility aids|"
    r"(?:walks?|moves?|walking|moving|able to walk)(?: and (?:walk|move|moves))? unaided|"
    r"able-bodied(?: with unrestricted walking)?|unrestricted walking|"
    r"full mobility(?: on all four legs)?|accessible)\b", re.I,
)
_NON_VISUAL = re.compile(
    r"\b(?:personality|loyal|brave|clever|intelligent|hardworking|lazy|"
    r"kind-hearted|relationship|symbolizes?|represents?|lasting happiness)\b", re.I,
)

# Keys stay internal: no larger JSON schema or additional LLM pass is needed.
_TRAITS = (
    ("condition", r"\b(?:intact|collapsed|destroyed|ruined|rebuilt|burned|flooded)\b", r"\b(?:intact|collapsed|destroyed|ruined|rebuilt|burned|flooded|intact[oa]|derrumbad[oa]|destruid[oa]|ruinas|reconstruid[oa]|quemad[oa]|inundad[oa])\b"),
    ("decoration", r"\b(?:flower pots?|paintings?|curtains?)\b", r"\b(?:flower pots?|paintings?|curtains?|macetas?|cuadros?|cortinas?)\b"),
    ("eyes", r"\beyes?\b", r"\b(?:eyes?|ojos?)\b"),
    ("ears", r"\bears?\b", r"\b(?:ears?|orejas?)\b"),
    ("hair", r"\b(?:hair|bristles|braids?|beard|mane)\b", r"\b(?:hair|bristles|braids?|beard|mane|pelo|cabello|cerdas|trenzas?|barba|melena)\b"),
    ("snout", r"\b(?:snout|muzzle)\b", r"\b(?:snout|muzzle|hocico)\b"),
    ("nose", r"\bnose\b", r"\b(?:nose|nariz)\b"),
    ("face", r"\b(?:face|cheeks?)\b", r"\b(?:face|cheeks?|cara|rostro|mejillas?)\b"),
    ("surface", r"\b(?:skin|fur|complexion|feathers?|scales)\b", r"\b(?:skin|fur|complexion|feathers?|scales|piel|pelaje|tez|plumas?|escamas)\b"),
    ("aid", r"\b(?:wheelchair|crutch|cane|walker|prosthe\w*|speech synthesizer|mobility aid)\b", r"\b(?:wheelchair|crutch|cane|walker|prosthe\w*|speech synthesizer|mobility aid|silla de ruedas|muletas?|baston|andador|protesis|sintetizador de voz)\b"),
    ("glasses", r"\b(?:glasses|spectacles)\b", r"\b(?:glasses|spectacles|gafas|anteojos|lentes)\b"),
    ("neckwear", r"\b(?:neckerchief|scarf|collar|tie)\b", r"\b(?:neckerchief|scarf|collar|tie|panuelo|bufanda|corbata)\b"),
    ("footwear", r"\b(?:shoes?|boots?|sandals?)\b", r"\b(?:shoes?|boots?|sandals?|zapatos?|botas?|sandalias?)\b"),
    ("bottom", r"\b(?:trousers|pants|shorts|jeans|skirt)\b", r"\b(?:trousers|pants|shorts|jeans|skirt|pantalones|vaqueros|falda)\b"),
    ("outfit", r"\b(?:dress|robe|suit|overalls|tunic)\b", r"\b(?:dress|robe|suit|overalls|tunic|vestido|tunica|traje|mono)\b"),
    ("outerwear", r"\b(?:jacket|coat|apron)\b", r"\b(?:jacket|coat|apron|chaqueta|abrigo|delantal)\b"),
    ("top", r"\b(?:shirt|blouse|vest|hoodie|sweater)\b", r"\b(?:shirt|blouse|vest|hoodie|sweater|camisa|blusa|chaleco|sudadera|jersey)\b"),
    ("headwear", r"\b(?:hat|cap|headscarf|helmet|crown)\b", r"\b(?:hat|cap|headscarf|helmet|crown|sombrero|gorra|casco|corona)\b"),
    ("age", r"\b(?:years?[ -]old|year-old|aged|newborn|baby|infant|child|young|adult|elderly|older|teen\w*|twenties|thirties)\b", r"\b(?:years? old|years? later|decades? later|aged|newborn|baby|infant|child|young|adult|elderly|older|teen\w*|children|anos|decadas|edad|bebe|ninos?|ninas?|joven|adult[oa]|ancian[oa]|mayor)\b"),
    ("build", r"\b(?:build|bodied|shouldered|stature|height)\b", r"\b(?:build|bodied|shouldered|stature|height|complexion|estatura|alto|bajo|delgad[oa]|robust[oa])\b"),
    ("roof", r"\broof\b", r"\b(?:roof|tejado|techo)\b"),
    ("door", r"\bdoors?\b", r"\b(?:doors?|puertas?)\b"),
    ("windows", r"\bwindows?|shutters?\b", r"\b(?:windows?|shutters?|ventanas?|contraventanas?)\b"),
    ("architecture", r"\b(?:house|cottage|walls?|masonry|timber|building)\b", r"\b(?:house|cottage|walls?|masonry|timber|building|casa|cabana|paredes|muros|ladrillos|madera|edificio)\b"),
    ("subject", r"\b(?:piglet|pig|sow|wolf|dog|cat|woman|man|girl|boy|female|male|animal)\b", r"\b(?:piglet|pig|sow|wolf|dog|cat|woman|man|girl|boy|female|male|animal|cerdit[oa]|cerdo|lobo|perro|gato|mujer|hombre)\b"),
)


def _key(text: str) -> str:
    for key, pattern, _ in _TRAITS:
        if re.search(pattern, text, re.I):
            return key
    return "detail:" + _fold(text).strip()


def visual_clauses(text: str) -> list[str]:
    text = _DEFAULT_MOBILITY.sub("", str(text or ""))
    # Separate an age from its outfit, and separate independent visual traits.
    text = re.sub(r"\s+(?=wearing\b|dressed in\b)", ", ", text, flags=re.I)
    text = re.sub(r"\s+(?:with|using)\s+", ", ", text, flags=re.I)
    result: list[str] = []
    for part in re.split(r"[,;]|\.\s+", text):
        part = re.sub(r"^(?:and|with)\s+|\s+(?:and|with)$", "", part.strip(" .;,"), flags=re.I).strip()
        if not part:
            continue
        if _key(part).startswith("detail:") and _NON_VISUAL.search(part):
            continue
        halves = re.split(r"\s+and\s+", part, maxsplit=1, flags=re.I)
        if len(halves) == 2 and all(not _key(v).startswith("detail:") for v in halves):
            result.extend(visual_clauses(halves[0]))
            result.extend(visual_clauses(halves[1]))
        else:
            result.append(part)
    return result


def _source_supports(key: str, text: str, evidence: str) -> bool:
    """Conservative admission gate; the LLM must supply only source deltas.

    A subject's mere mention is NOT evidence for a new physical design.
    Unknown phrasing is admitted only when quoted literally in the source.
    """
    folded = _fold(evidence)
    if _fold(text) in folded:
        return True
    if key == "subject":
        return False
    cue = next((cue for name, _, cue in _TRAITS if name == key), "")
    if not cue or not re.search(cue, folded):
        return False
    # Mentioning eyes/ears/age alone is not proof of a guessed color/shape/age.
    # These common bilingual discriminators protect source provenance. Unknown
    # languages/phrasing remain conservative; they never unlock existing facts.
    values = (
        (r"\bgreen\b", r"\b(?:green|verdes?)\b"),
        (r"\bblue\b", r"\b(?:blue|azul(?:es)?)\b"),
        (r"\b(?:brown|chestnut)\b", r"\b(?:brown|chestnut|marron(?:es)?|castan[oa]s?|pard[oa]s?)\b"),
        (r"\bblack\b", r"\b(?:black|negr[oa]s?)\b"),
        (r"\bwhite\b", r"\b(?:white|blanc[oa]s?)\b"),
        (r"\b(?:gray|grey)\b", r"\b(?:gray|grey|gris(?:es)?)\b"),
        (r"\bred\b", r"\b(?:red|roj[oa]s?)\b"),
        (r"\byellow\b", r"\b(?:yellow|amarill[oa]s?)\b"),
        (r"\b(?:blonde?|fair)\b", r"\b(?:blonde?|fair|rubi[oa]s?)\b"),
        (r"\bhazel\b", r"\b(?:hazel|avellana)\b"),
        (r"\bfloppy\b", r"\b(?:floppy|caidas?)\b"),
        (r"\bupright\b", r"\b(?:upright|erguidas?|erectas?)\b"),
    )
    for value, translated in values:
        if re.search(value, text, re.I) and not re.search(translated, folded):
            return False
    if key == "age":
        numbers = re.findall(r"\d+", text)
        if any(number not in re.findall(r"\d+", evidence) for number in numbers):
            return False
    return True


def merge_visual_description(
    current: str, incoming: str, *, evidence: str = "", unit_id: int = 0,
    metadata: dict[str, Any] | None = None, initial: bool = False,
    explicit_change: bool = False, source_claimed: bool = False,
) -> tuple[str, dict[str, Any]]:
    """Lock generated traits; only supported deltas amend a known design."""
    current = str(current or "").strip()
    metadata = metadata if isinstance(metadata, dict) else {}
    if metadata.get("text") == current and isinstance(metadata.get("facts"), dict):
        facts = {k: dict(v) for k, v in metadata["facts"].items() if isinstance(v, dict)}
    else:
        # No matching provenance means legacy/manual content: never treat it as
        # a disposable invented choice. Plain text remains the UI's authority.
        facts = {}
        for clause in visual_clauses(current):
            facts.setdefault(_key(clause), {"text": clause, "origin": "user_or_legacy"})
    for clause in visual_clauses(incoming):
        key = _key(clause)
        supported = _source_supports(key, clause, evidence) or (source_claimed and not initial)
        if not initial and not supported:
            continue
        old = facts.get(key)
        if old:
            if _fold(clause) == _fold(str(old.get("text") or "")):
                if supported:
                    old.update(origin="source", evidence=evidence, source_unit=unit_id)
                continue
            if initial:
                continue  # One value per trait, including malformed first drafts.
            if not explicit_change and old.get("origin") != "inferred":
                # A source can expand "brown hair" into "long brown hair"
                # without contradicting it. Manual/legacy locks remain intact.
                extension = old.get("origin") == "source" and _fold(str(old.get("text") or "")) in _fold(clause)
                if not extension:
                    continue
        facts[key] = {"text": clause, "origin": "source" if supported else "inferred",
                      "evidence": evidence if supported else "", "source_unit": unit_id}
        if not initial:
            # A shirt/trousers replaces a one-piece outfit, not its shoes or
            # coat. A new dress replaces the old top/bottom, not all wardrobe.
            for incompatible in ({"top", "bottom"} if key == "outfit" else {"outfit"} if key in {"top", "bottom"} else set()):
                facts.pop(incompatible, None)
    rendered = ", ".join(str(value["text"]) for value in facts.values())
    return rendered, {"text": rendered, "facts": facts}


def compact_visual_description(*descriptions: str, max_words: int = 45) -> str:
    """Short compiler view, never a destructive migration of project text.

    First value wins for legacy duplicate slots. Keep real aids even at the
    budget boundary. Do not truncate a sentence mid-word or alter raw overrides.
    """
    facts: dict[str, str] = {}
    collision = False
    for description in descriptions:
        local: dict[str, str] = {}
        for clause in visual_clauses(description):
            key = _key(clause)
            if key in local:
                collision = True
            local.setdefault(key, clause)
        if any(key in facts for key in local):
            collision = True
        # A selected temporal state takes precedence over the permanent field
        # if an older/manual project accidentally stored a mutable trait there.
        facts.update(local)
    original = ", ".join(str(d).strip(" .") for d in descriptions if str(d).strip())
    if not collision and not _DEFAULT_MOBILITY.search(original) and not _NON_VISUAL.search(original) and len(original.split()) <= max_words:
        return original
    selected: set[str] = set()
    words = 0
    priority = {"aid", "glasses", "age", "neckwear", "outfit", "top", "bottom", "outerwear"}
    for key in sorted(facts, key=lambda k: k not in priority):
        clause = facts[key]
        count = len(clause.split())
        if words + count <= max_words or key in {"aid", "glasses"}:
            selected.add(key)
            words += count
    return ", ".join(clause for key, clause in facts.items() if key in selected)


def limit_generated_identity(text: str, metadata: dict[str, Any],
                             *, max_words: int = 35, max_traits: int = 5) -> tuple[str, dict[str, Any]]:
    """Keep a small reusable portrait, not an ever-growing inventory.

    Source facts take precedence over invented details. Keep whole clauses,
    with original ordering, and never shorten manual/legacy definitions.
    Discovery reports retain the fuller source analysis.
    """
    facts = metadata.get("facts", {})
    if metadata.get("text") != text or not facts or any(
        value.get("origin") == "user_or_legacy" for value in facts.values()
    ):
        return text, metadata
    selected: set[str] = set()
    words = 0
    for key in sorted(facts, key=lambda k: facts[k].get("origin") != "source"):
        count = len(str(facts[key].get("text") or "").split())
        if len(selected) < max_traits and (words + count <= max_words or not selected):
            selected.add(key)
            words += count
    kept = {key: value for key, value in facts.items() if key in selected}
    rendered = ", ".join(str(value["text"]) for value in kept.values())
    return rendered, {**metadata, "text": rendered, "facts": kept}
