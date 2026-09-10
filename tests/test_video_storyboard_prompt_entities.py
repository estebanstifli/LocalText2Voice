from app.core.video_storyboard_comfyui import compile_scene_prompt
from app.core.video_storyboard_prompt_entities import (
    canonical_location_state_ids_for_prompt,
    decorate_storyboard_prompt,
    strip_storyboard_prompt_markers,
)


def _plan() -> dict:
    return {
        "style": {"medium": "comic book"},
        "continuity": {
            "characters": [
                {
                    "id": "ana",
                    "name": "Ana",
                    "identity_description": "young woman with dark curls",
                    "states": [
                        {
                            "id": "ana_blue_coat",
                            "description": "blue coat",
                            "from_seconds": 0,
                            "to_seconds": 20,
                        }
                    ],
                }
            ],
            "locations": [
                {
                    "id": "central_plaza",
                    "name": "Central Plaza",
                    "identity_description": "stone square with a fountain",
                    "states": [
                        {
                            "id": "central_plaza_day",
                            "description": "sunny morning",
                            "from_seconds": 0,
                            "to_seconds": 20,
                        }
                    ],
                }
            ],
        },
    }


def test_prompt_markup_is_visible_but_never_reaches_compiled_prompt() -> None:
    plan = _plan()
    decorated = decorate_storyboard_prompt(
        "Ana crosses Central Plaza.",
        plan,
    )
    assert decorated == "@Ana crosses @Central Plaza."
    assert strip_storyboard_prompt_markers(decorated, plan) == (
        "Ana crosses Central Plaza."
    )
    scene = {
        "prompt": decorated,
        "shot": "Medium view of @Ana",
        "characters": ["ana_blue_coat"],
        "locations": ["central_plaza_day"],
    }
    compiled = compile_scene_prompt(plan, scene)
    assert "@" not in compiled
    assert "Ana crosses Central Plaza" in compiled
    assert "stone square with a fountain" in compiled


def test_location_mention_resolves_the_state_active_at_scene_time() -> None:
    plan = _plan()
    result = canonical_location_state_ids_for_prompt(
        plan,
        {"start_seconds": 4, "duration_seconds": 6},
        "@Central Plaza under morning light",
    )
    assert result == ["central_plaza_day"]
