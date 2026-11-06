from ui_i18n import STRINGS, streaming_hint_key


def test_streaming_hint_matches_input_capability():
    readonly = streaming_hint_key(None)
    control = streaming_hint_key(lambda method, params: None)
    assert readonly == "streaming_hint_readonly"
    assert control == "streaming_hint_control"
    for translations in STRINGS.values():
        assert translations[readonly] != translations[control]
