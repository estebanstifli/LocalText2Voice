"""Versioned analysis policies; provider JSON contracts are not user-editable."""
from copy import deepcopy

VERSION = 1
PROMPTS = {
    "conversation_report": ("Characters, appearance, changes and story summary",
        "Which characters appear in this story? State each character's species or kind from the story, including unnamed relatives. What do they look like and wear? Does any character change their clothing, appearance, or age? Also add a 4–5-line summary of what the story is about. Use sections: Characters (Name: species and appearance), Changes, Summary."),
    "conversation_additions": ("New characters from the next report",
        "Which characters appear in the second text but not in the first? List only those characters and their descriptions."),
    "conversation_characters": ("Characters and story summary",
        "Which characters appear in this story? Does any character change their clothing, appearance, or age? Also add a brief 2–3-line summary of what the story is about."),
    "conversation_scenes": ("Proposed scenes and source sentences",
        "What scenes would you illustrate in this excerpt? For each, briefly describe the image and quote its opening sentence."),
    "conversation_appearance": ("Character appearance",
        "What does each character look like and wear? Give one consistent visual design per character, not alternatives. For humans only, specify hair length and color, or baldness. Respect the story; if these details are missing, choose them once. Keep the initial appearance separate from any clothing or appearance changes explicitly described in the story."),
    "characters": ("Identify characters and groups",
        "Which characters appear in this story? Also add a brief 2–3-line summary of what the story is about."),
    "character_registry": ("Character identities and groups",
        "Organize the report into individual identities, not visual designs. Include every referenced individual, including identifiable members introduced as a group. Reuse a known ID only for that same individual; leave known_id empty for a new identity. Group entries name their individual members; do not also create a group as an individual. Preserve aliases and first unit numbers. Consult the original passage to resolve doubts."),
    "character_appearance": ("Character appearance and evolution",
        "Write one short visual portrait, at most 25 words, for each NEW identity supplied. Preserve species and source-described facts. Where details are missing, choose a few compatible distinctive visual anchors so individuals remain distinguishable. Do not invent exact ages, injuries, normal-mobility statements or relationships. Keep known designs unchanged. List a change only for a real physical evolution such as aging or different clothing; list a revelation when a previously true visual fact is first disclosed. Describe the COMPLETE resulting appearance in those entries, keeping unaffected traits. Actions, feelings and temporary carried props do not create changes. Both lists are normally empty."),
    "locations": ("Place report",
        "Write a short plain-text report identifying the physical places in this passage. Describe the few visual facts given and any actual construction, destruction or restoration, citing unit numbers. Different names may refer to the same place; separate genuinely different places. Finding an existing feature is not building it. Do not invent missing details or analyze characters."),
    "location_registry": ("Place identities",
        "Organize the place report into distinct recurring places. Reuse a known ID only when it is the SAME place, not merely another house or room. Otherwise leave known_id empty. Include aliases and the first applicable unit number. Consult the original passage; actions at a place are not new places."),
    "location_appearance": ("Place appearance and evolution",
        "Give each NEW place a brief concrete visual description of at most 25 words. Preserve stated materials and layout; choose a few compatible details only where missing. Keep known places unchanged. List changes only for actual physical alterations such as construction, damage or restoration. Discovering a pre-existing feature is a revelation, not a change. Each update gives the COMPLETE current appearance, retaining unaffected details. Occupants, actions and moods are not changes. Both update lists are normally empty."),
    "era": ("Historical period",
        'Extract only explicit historical dates or named historical periods from the current narration. '
        'Each event needs a current unit ID and an exact quote from that unit naming the date or period. '
        'Known eras are context, not evidence. Return {"era_events": []} when no new date or period is stated. '
        'Do not infer dates from materials, buildings, customs or actions. '
        'Leave material_culture empty unless historically relevant material details are explicitly stated.'),
}


def defaults():
    return {"process": "compact", "block_size": "medium", "custom_characters": 4000,
            "era_mode": "detect", "prompts": {}}


def normalize(value):
    result = defaults()
    if isinstance(value, dict):
        for key, choices in {"process": ("compact", "simple", "conversational"), "block_size": ("small", "medium", "custom"),
                             "era_mode": ("detect", "provided")}.items():
            if value.get(key) in choices:
                result[key] = value[key]
        try:
            result["custom_characters"] = max(1000, min(100000, int(value.get("custom_characters", 4000))))
        except (TypeError, ValueError):
            pass
        if isinstance(value.get("prompts"), dict):
            result["prompts"] = {k: v for k, v in value["prompts"].items()
                                 if k in {"discovery", "structure", "binding", *PROMPTS} and isinstance(v, str) and v.strip()}
    return result


def prompt_catalog():
    # Read the actual defaults used by the compact planner rather than keep
    # a second copy that could drift from the real requests.
    from app.core import video_storyboard_planner as p
    return {
        "discovery": ("Combined discovery", p._continuity_discovery_messages([])[0]),
        "structure": ("Combined continuity", p._continuity_analyzer_messages([], {}, [], era_request="")[0]),
        **PROMPTS,
        "binding": ("Narration unit binding", p._unit_binder_messages([], {}, [])[0]),
    }


def instruction(config, stage):
    return config.get("prompts", {}).get(stage) or prompt_catalog()[stage][1]


def input_limit(config):
    return {"small": 2000, "medium": 4000}.get(config["block_size"], config["custom_characters"])


def conversation_input_limit(config):
    return {"small": 4000, "medium": 17000}.get(config["block_size"], config["custom_characters"])


def snapshot(settings):
    config = normalize(settings.get("continuity_analysis"))
    provider = str(settings.get("llm_provider") or "ollama")
    model = settings.get(provider, {})
    return {"version": VERSION, **deepcopy(config),
            "process_revision": 4 if config["process"] == "simple" else 2,
            "protected_contract": CONTRACT,
            "protected_contracts": {k: contract_for(k) for k in prompt_catalog()},
            "effective_prompts": {k: instruction(config, k) for k in prompt_catalog()},
            "provider": provider, "model": model.get("model", ""),
            "limits": deepcopy(settings.get("analysis", {})),
            "model_parameters": {k: model[k] for k in ("max_output_tokens", "temperature", "num_ctx", "think") if k in model}}


CONTRACT = (
    "Follow the supplied response schema exactly. Use only supplied narration unit IDs; reuse known entity IDs. "
    "Descriptions are concise English visual facts; evidence quotes the source language. "
    "Keep identity under 25 words, appearance under 30 words total. "
    "Appearance null means unchanged; empty string explicitly removes a previous value. "
    "Only real physical changes create temporal states. Never invent mobility or describe absence of disability. "
    "Never treat locations as characters. Groups contain individual character IDs, not a second visual identity."
)


def contract_for(stage):
    if stage.startswith("conversation_"):
        return "Plain-text conversation with shared message history. No timestamps or JSON schema in these discovery turns."
    if stage == "characters":
        return ""
    if stage == "era":
        return "Return only the supplied JSON schema. Use only allowed current unit IDs and lowercase_snake_case event IDs. Do not repeat known events."
    if stage in {"discovery", "characters", "locations"}:
        return "Return a plain-text report using only the supplied narration as evidence. Preserve unit IDs and names. Do not generate images or anticipate events outside this block."
    if stage == "binding":
        return "Return the supplied JSON schema. Exactly one binding per supplied unit, in order. Use only supplied entity IDs; do not invent or redesign entities."
    if stage in {"character_registry", "location_registry"}:
        return "Return only the small supplied JSON schema. Use source names and supplied unit numbers. known_id must be empty for new entities or exactly a supplied ID. Return empty lists when nothing applies."
    if stage in {"character_appearance", "location_appearance"}:
        return "Return only the supplied JSON: definitions for NEW supplied names, changes and revelations lists. Use the exact supplied names and unit IDs. Each description is a short complete English visual profile, not a list of mandatory attributes. Evidence must quote the original passage verbatim. No update means an empty list, never a filler such as none or unchanged. Do not create identities."
    return CONTRACT


def character_discovery_messages(text, config=None):
    """The simple evidence pass sees narration only, without planner metadata."""
    return instruction(normalize(config), "characters"), text


def story_synopsis(report):
    """Prefer the conversational synopsis section without requiring a JSON format."""
    import re
    match = re.search(r"(?im)^\s*(?:#{1,6}\s*)?(?:\*\*)?(?:(?:brief|story)\s+)?summary\s*:\s*(?:\*\*)?\s*", report)
    return report[match.end():].strip() if match else report.strip()


def request_continuity(request, settings, schema, system, user, *, context, **kwargs):
    from app.core.storyboard_analysis_review import choices, apply_basic_policy
    basic = choices(settings.get("analysis_choices"))["plan"] == "basic"
    config = normalize(settings.get("continuity_analysis"))
    if config["process"] == "compact":
        policy = instruction(config, "structure") + "\n" + CONTRACT
        if basic:
            policy += "\nCreate one stable initial visual profile per identity. Do not create temporal appearance changes or era events."
        if settings.get("reviewed_summary") is not None:
            import json
            user += "\nUser-approved summaries are authoritative for identities and appearance; use narration for timing only:\n" + json.dumps(settings["reviewed_summary"], ensure_ascii=False)
        if config["era_mode"] == "provided":
            policy += "\nDo not detect periods; return era_events as an empty array. The project supplies any era."
        result = request(settings, schema, policy, user, **kwargs)
        if config["era_mode"] == "provided":
            result["era_events"] = []
        return apply_basic_policy(result) if basic else result
    from app.core.storyboard_simple_continuity import run_simple
    result = run_simple(request, settings, schema, context=context, config=config, kwargs=kwargs,
                        instruction=instruction, contract_for=contract_for)
    return apply_basic_policy(result) if basic else result
