"""The conversation for action, as a transition table.

Specified in ``docs/assignment.md``. Two vocabularies, and keeping them apart is
the whole of this module:

**Moves** are what a party does — ``offer``, ``counter``, ``accept``, ``decline``,
``withdraw``, ``deliver``, ``settle``, ``return``, ``redirect``.

**States** are where the conversation rests between moves.

They are nearly the same list, which is why the spec first wrote them as one and
was ambiguous about ``returned``: a return does not rest anywhere, it puts the
work back in the delegate's hands. So ``return`` is a move whose resulting state
is ``accepted``. It is the one move that does not name its own state, and the
history still records it, which is what keeps "we went round three times"
legible.

**No terminal state here ends the commitment.** Nothing in this module can — the
intention lives in another system and docket cannot write to it. That is a
structural guarantee rather than a rule someone has to remember, and it is the
main reason the two are separate services.
"""

from typing import Literal

Move = Literal[
    "offer", "counter", "accept", "decline", "withdraw", "deliver", "settle", "return",
    "redirect", "update",
]
State = Literal[
    "offered", "countered", "accepted", "delivered", "settled", "declined", "withdrawn", "redirected"
]
Role = Literal["requester", "delegate"]

MOVES: tuple[str, ...] = (
    "offer", "counter", "accept", "decline", "withdraw", "deliver", "settle", "return",
    "redirect", "update",
)

#: Says something happened without moving the conversation. Either party, any
#: state that is still open.
#:
#: Not in Winograd & Flores, and added because leaving it out forced a lie: an
#: agent that is stuck but still holding the work had only `decline` (give it
#: back) or silence. Silence is what S21 asks us to make visible, so a party with
#: something to say and no way to say it is the one case that must not exist.
#:
#: The cost is real and worth stating: staleness is measured from the last move,
#: so a party that reports nothing useful every hour looks attentive. An update
#: is evidence that somebody spoke, never that anything progressed.
STATE_PRESERVING: frozenset[str] = frozenset({"update"})
STATES: tuple[str, ...] = (
    "offered", "countered", "accepted", "delivered", "settled", "declined", "withdrawn", "redirected",
)

#: A conversation that has ended. The *commitment* has not — see the module note.
TERMINAL: frozenset[str] = frozenset({"settled", "declined", "withdrawn", "redirected"})

#: ``(state before, move) -> (state after, who may make it)``. ``None`` before
#: means the entry does not exist yet.
TRANSITIONS: dict[tuple[str | None, str], tuple[str, str]] = {
    (None, "offer"): ("offered", "requester"),

    ("offered", "accept"): ("accepted", "delegate"),
    ("offered", "counter"): ("countered", "delegate"),
    ("offered", "decline"): ("declined", "delegate"),
    ("offered", "redirect"): ("redirected", "delegate"),
    ("offered", "withdraw"): ("withdrawn", "requester"),

    # A counter puts it back to the requester, who may take the new terms,
    # re-offer on revised ones, or give up. The delegate may still walk away.
    ("countered", "accept"): ("accepted", "requester"),
    ("countered", "offer"): ("offered", "requester"),
    ("countered", "withdraw"): ("withdrawn", "requester"),
    ("countered", "decline"): ("declined", "delegate"),

    ("accepted", "deliver"): ("delivered", "delegate"),
    # Reneging is a real move and pretending otherwise would force it to be
    # recorded as something it is not.
    ("accepted", "decline"): ("declined", "delegate"),
    ("accepted", "redirect"): ("redirected", "delegate"),
    ("accepted", "withdraw"): ("withdrawn", "requester"),

    ("delivered", "settle"): ("settled", "requester"),
    ("delivered", "return"): ("accepted", "requester"),
    ("delivered", "withdraw"): ("withdrawn", "requester"),
}


class Refused(Exception):
    """A move that the conversation does not allow from where it stands."""


def apply(state: str | None, move: str, role: str | None) -> str:
    """The state after ``move``, or raise ``Refused``.

    ``role`` is the mover's relation to *this entry*. ``None`` means the caller
    is neither party, which is always refused: an assignment names two parties
    and a third cannot move it.
    """
    if move not in MOVES:
        raise Refused(f"unknown move '{move}'")
    if move in STATE_PRESERVING:
        if state is None:
            raise Refused(f"cannot {move} on a new entry")
        if state in TERMINAL:
            raise Refused(f"cannot {move} on a conversation that ended in '{state}'")
        if role is None:
            raise Refused(f"only a party may {move}")
        return state
    try:
        after, allowed = TRANSITIONS[(state, move)]
    except KeyError:
        where = "a new entry" if state is None else f"'{state}'"
        raise Refused(f"cannot {move} from {where}") from None
    if role != allowed:
        who = role or "someone who is not a party"
        raise Refused(f"only the {allowed} may {move}; caller is {who}")
    return after


def moves_available(state: str | None, role: str | None) -> list[str]:
    """Every move ``role`` could make right now. Drives a UI without duplicating
    the table above, which is how the two come to disagree."""
    if state is None or role is None:
        available = set()
    else:
        available = set() if state in TERMINAL else set(STATE_PRESERVING)
    available |= {
        m for (s, m), (_after, allowed) in TRANSITIONS.items()
        if s == state and allowed == role
    }
    return sorted(available)
