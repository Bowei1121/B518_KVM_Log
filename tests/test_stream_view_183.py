from ui_i18n import STRINGS, streaming_hint_key


def test_streaming_hint_matches_input_capability():
    """ADR-0008 revises #183: pop-out now passes an input_sender; None still means read-only."""
    readonly = streaming_hint_key(None)
    control = streaming_hint_key(lambda method, params: None)
    assert readonly == "streaming_hint_readonly"
    assert control == "streaming_hint_control"
    for translations in STRINGS.values():
        assert translations[readonly] != translations[control]


def test_control_hint_warns_automation_ignores_input():
    assert "ignored" in STRINGS["en"]["streaming_hint_control"]
    assert "忽略" in STRINGS["zh"]["streaming_hint_control"] if "zh" in STRINGS else True
