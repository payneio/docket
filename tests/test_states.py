"""The transition table, without a database in the way."""

import pytest

from docket import states


def test_an_entry_begins_with_an_offer_and_nothing_else():
    assert states.apply(None, "offer", "requester") == "offered"
    for move in set(states.MOVES) - {"offer"}:
        with pytest.raises(states.Refused):
            states.apply(None, move, "requester")


def test_return_is_a_move_whose_state_is_accepted():
    """The one move that does not name its own state. A return puts the work back
    in the delegate's hands; it does not rest anywhere of its own."""
    assert states.apply("delivered", "return", "requester") == "accepted"
    assert "returned" not in states.STATES


def test_only_the_named_party_may_move():
    assert states.apply("offered", "accept", "delegate") == "accepted"
    with pytest.raises(states.Refused, match="only the delegate"):
        states.apply("offered", "accept", "requester")
    with pytest.raises(states.Refused, match="not a party"):
        states.apply("offered", "accept", None)


def test_a_counter_hands_the_decision_back():
    """After a counter it is the *requester* who accepts — the terms on the table
    are now the delegate's, so accepting them is the requester's move."""
    assert states.apply("offered", "counter", "delegate") == "countered"
    assert states.apply("countered", "accept", "requester") == "accepted"
    with pytest.raises(states.Refused):
        states.apply("countered", "accept", "delegate")


def test_reneging_is_expressible():
    """Declining after accepting is a real thing people do. Forcing it to be
    recorded as a withdrawal would put it on the wrong party."""
    assert states.apply("accepted", "decline", "delegate") == "declined"


def test_nothing_moves_out_of_a_terminal_state():
    for state in states.TERMINAL:
        for move in states.MOVES:
            for role in ("requester", "delegate"):
                with pytest.raises(states.Refused):
                    states.apply(state, move, role)


def test_available_moves_come_from_the_same_table_the_server_enforces():
    for state in states.STATES:
        for role in ("requester", "delegate"):
            for move in states.moves_available(state, role):
                states.apply(state, move, role)  # must not raise


def test_an_update_says_something_without_moving_anything():
    """A party that is stuck but still holding the work had only `decline` or
    silence. Silence is the failure S21 names, so this had to exist."""
    for state in ("offered", "countered", "accepted", "delivered"):
        for role in ("requester", "delegate"):
            assert states.apply(state, "update", role) == state
            assert "update" in states.moves_available(state, role)


def test_you_cannot_update_a_conversation_that_ended():
    for state in states.TERMINAL:
        with pytest.raises(states.Refused):
            states.apply(state, "update", "delegate")
        assert "update" not in states.moves_available(state, "delegate")


def test_a_stranger_cannot_update_either():
    with pytest.raises(states.Refused, match="only a party"):
        states.apply("accepted", "update", None)
