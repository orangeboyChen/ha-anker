"""Control probe verification semantics never turn a no-op into an ACK."""

from tools.anker_mqtt_control_debug import assess_port_write


def test_same_value_readback_is_explicitly_unconfirmed() -> None:
    result = assess_port_write(
        before_enabled=False,
        after_enabled=False,
        requested_enabled=False,
        ack=False,
    )

    assert result == {
        "readback_matches_request": True,
        "state_changed_to_request": False,
        "write_confirmed": False,
        "verification": "unconfirmed_no_change",
    }


def test_state_transition_is_readback_evidence_not_an_ack() -> None:
    result = assess_port_write(
        before_enabled=True,
        after_enabled=False,
        requested_enabled=False,
        ack=False,
    )

    assert result["readback_matches_request"] is True
    assert result["state_changed_to_request"] is True
    assert result["write_confirmed"] is False
    assert result["verification"] == "state_changed_on_readback"


def test_explicit_ack_is_distinguished() -> None:
    result = assess_port_write(
        before_enabled=False,
        after_enabled=False,
        requested_enabled=False,
        ack=True,
    )

    assert result["write_confirmed"] is True
    assert result["verification"] == "acknowledged"
