"""References to things docket does not own.

Every party and every subject is a **URI**, never a foreign key. Docket holds
what is owed between parties; it does not hold the parties, and it does not hold
the thing owed. Both live in whatever system authored them.

Two forms are in use:

``wild-life:person:<uuid>``
    Something in another system on this machine. ``<system>:<type>:<id>``.

``mailto:sara@example.com``
    A party with no record anywhere here. This is the same address form iTIP
    uses for ``ATTENDEE`` (RFC 5545 CAL-ADDRESS), which is why an external
    delegate needs no nullable label column: the reference *is* the address.

The point of a URI rather than a foreign key is that an entry stays meaningful
when the system on the other end is unreachable, renamed, or gone. What it costs
is that nothing here can enforce that the far end exists — which is correct.
Docket must not require wild-life to be running.
"""

import re

_REF = re.compile(r"^[a-z][a-z0-9+.-]*:[^\s]+$", re.IGNORECASE)


def is_ref(value: str) -> bool:
    """A syntactically valid reference."""
    return bool(value) and bool(_REF.match(value))


def parts(ref: str) -> tuple[str, str | None, str | None]:
    """Split ``system:type:id`` into its three parts.

    A two-part reference such as ``mailto:sara@example.com`` returns
    ``("mailto", None, "sara@example.com")`` — there is no type to report, and
    inventing one ("person") would assert something the address does not say.
    """
    scheme, _, rest = ref.partition(":")
    if not rest:
        return scheme, None, None
    kind, sep, ident = rest.partition(":")
    if not sep:
        return scheme, None, rest
    return scheme, kind, ident
