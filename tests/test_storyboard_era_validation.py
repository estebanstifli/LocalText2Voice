from copy import deepcopy
import json

from app.core.storyboard_analysis_settings import request_continuity, contract_for
from app.core.storyboard_simple_continuity import validated_era_events
from app.core.video_storyboard_planner import _continuity_analyzer_schema, _continuity_analyzer_validation_issues


UNITS = [{"id": 33, "text": "In 1850, Clara lived in London."},
         {"id": 35, "text": "A century later, in 1950, her granddaughter returned."}]


def event(unit=33, evidence="In 1850", **changes):
    return {"id": "era_1850", "effective_unit_id": unit, "description": "1850",
            "material_culture": "", "evidence": evidence, **changes}


def test_valid_dates_survive_and_malformed_events_only_warn():
    warnings = []
    valid = event()
    result = validated_era_events({"era_events": [valid, valid, event(19),
        event(evidence="Medieval peasants built straw houses."),
        event(id="Bad ID"), event(material_culture=None), None,
        event(evidence=""), event(description="unknown"), event(True)]}, UNITS, warnings)
    assert result == [valid]
    assert len(warnings) == 8
    issues = _continuity_analyzer_validation_issues(
        {"character_events": [], "location_events": [], "era_events": result, "groups": []}, UNITS, {})
    assert not any("era_events" in issue for issue in issues)


def test_no_guessing_missing_period_or_reanchoring_previous_block():
    warnings = []
    assert validated_era_events({"era_events": []}, UNITS, warnings) == []
    assert warnings == []
    assert validated_era_events({"era_events": [event(19)]}, UNITS, warnings) == []
    assert "outside the current block" in warnings[-1]
    assert validated_era_events({"era_events": "bad"}, UNITS, warnings) == []


def test_era_request_is_bounded_reduced_and_invalid_response_does_not_abort():
    calls = []
    ledger = {"eras": [{"id": "old", "description": "medieval",
                         "evidence": "invented old evidence", "material_culture": "irrelevant"}]}
    original = deepcopy(ledger)
    def request(settings, schema, system, user, **kwargs):
        calls.append((schema, system, json.loads(user), kwargs))
        if kwargs["request_label"].endswith(" / era"):
            return {"era_events": [event(19), event()]}
        if schema is None:
            return "No places."
        return {key: [] for key in schema["properties"]}
    result = request_continuity(request, {"continuity_analysis": {"process": "simple"}},
        _continuity_analyzer_schema(), "", "", context=(UNITS, ledger, "Clara", ""))
    schema, system, payload, _ = calls[-1]
    assert schema["properties"]["era_events"]["items"]["properties"]["effective_unit_id"]["enum"] == [33, 35]
    assert payload["known_eras"] == [{"id": "old", "description": "medieval"}]
    assert "mobility" not in system and "character" not in system
    assert result["era_events"] == [event()]
    assert result["_simple_warnings"]
    assert ledger == original
    assert "unit IDs" in contract_for("era")


def test_empty_input_does_not_send_invalid_empty_enum():
    def request(settings, schema, system, user, **kwargs):
        assert not kwargs["request_label"].endswith(" / era")
        return "" if schema is None else {key: [] for key in schema["properties"]}
    result = request_continuity(request, {"continuity_analysis": {"process": "simple"}},
        _continuity_analyzer_schema(), "", "", context=([], {}, "", ""))
    assert result["era_events"] == []
