"""Conversational analysis configuration; old process selectors are migrated on load."""
from copy import deepcopy
from app.core.storyboard_eras import DISCOVERY

VERSION = 2
PROMPTS = {
    "conversation_report": (
        "Characters, appearance, changes and story summary",
        "Which characters appear in this story? State each character's species or kind from the story, "
        "including unnamed relatives. What do they look like and wear? Does any character change their "
        "clothing, appearance, or age? Also add a 4–5-line summary of what the story is about. "
        "Use sections: Characters (Name: species and appearance), Changes, Summary.",
    ),
    "conversation_additions": (
        "New characters from the next report",
        "Which characters appear in the second text but not in the first? List only those characters and their descriptions.",
    ),
    "conversation_scenes": (
        "Proposed scenes and source sentences",
        "What scenes would you illustrate in this excerpt? For each, briefly describe the image and quote its opening sentence.",
    ),
    "conversation_eras": ("Historical periods and visual context", DISCOVERY),
}


def defaults():
    return {"process": "conversational", "block_size": "medium", "custom_characters": 4000, "prompts": {}}


def normalize(value):
    result = defaults()
    if isinstance(value, dict):
        if value.get("block_size") in {"small", "medium", "custom"}:
            result["block_size"] = value["block_size"]
        try:
            result["custom_characters"] = max(1000, min(100000, int(value.get("custom_characters", 4000))))
        except (TypeError, ValueError):
            pass
        if isinstance(value.get("prompts"), dict):
            result["prompts"] = {k: v for k, v in value["prompts"].items()
                                 if k in PROMPTS and isinstance(v, str) and v.strip()}
    return result


def prompt_catalog():
    return deepcopy(PROMPTS)


def instruction(config, stage):
    return config.get("prompts", {}).get(stage) or PROMPTS[stage][1]


def conversation_input_limit(config):
    return {"small": 4000, "medium": 17000}.get(config["block_size"], config["custom_characters"])


# Compatibility for saved settings callers; there is only one execution pipeline.
input_limit = conversation_input_limit
CONTRACT = "Plain-text discovery followed by schema-validated JSON. The application aligns literal source quotes to narration cues."


def contract_for(stage):
    return "Plain-text report; no JSON or invented timestamps. Preserve source quotes and distinguish evidence from inference." if stage == "conversation_eras" else "Plain-text conversational discovery. Structured conversion and narration alignment are handled separately."


def snapshot(settings):
    config = normalize(settings.get("continuity_analysis"))
    provider = str(settings.get("llm_provider") or "ollama")
    model = settings.get(provider, {})
    return {"version": VERSION, **deepcopy(config), "process_revision": 3,
            "protected_contract": CONTRACT,
            "protected_contracts": {k: contract_for(k) for k in PROMPTS},
            "effective_prompts": {k: instruction(config, k) for k in PROMPTS},
            "provider": provider, "model": model.get("model", ""),
            "limits": deepcopy(settings.get("analysis", {})),
            "model_parameters": {k: model[k] for k in ("max_output_tokens", "temperature", "num_ctx", "think") if k in model}}
