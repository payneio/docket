"""Which run a move belongs to, by the standard that already says so.

A move records who made it and with which credential. What it did not record is
*which moves were made together* — one pass of a harness, one turn of cos, one
request from wild-life — so the activity page was a list where it should have
been a set of runs. W3C Trace Context is the standard for exactly this: a caller
sends ``traceparent``, and everything done on its behalf shares one trace id.

**The register keeps the id whether or not anything else is listening.** No
tracing SDK, collector or trace store is required for a move to know its run:
the context is extracted here with the API's own propagator, which is a few
dozen lines of header parsing, and ``service._record`` reads it from the
current context. A trace store expires and samples; the register is the
permanent record, so the id it needs lives in it.

**A trace id is asserted, like a note.** Any party can send any ``traceparent``,
including one copied from somebody else's run. What is attested about a move is
still ``by``, ``recorded_by`` and ``via_credential``; this says what the caller
claims it was part of, and the board says so.

When an OpenTelemetry SDK and the FastAPI instrumentation are installed they
extract the context themselves and start a server span; this middleware sees a
valid current span and stays out of the way.
"""

from opentelemetry import context, propagate, trace
from starlette.types import ASGIApp, Receive, Scope, Send


class TraceContext:
    """Attach the caller's W3C trace context for the life of the request.

    Plain ASGI rather than ``BaseHTTPMiddleware``: that one runs the app in a
    separate task, and a context attached here would not reach the handler. Also
    plain so the SSE stream is passed through untouched.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or trace.get_current_span().get_span_context().is_valid:
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope["headers"]}
        token = context.attach(propagate.extract(headers))
        try:
            await self.app(scope, receive, send)
        finally:
            context.detach(token)


def current_ids() -> tuple[str | None, str | None]:
    """The trace and span ids to write on a move, as lowercase hex.

    The trace id whenever the context is valid — that is the "same run" key, and
    it needs nothing but the caller's header.

    The span id only when the current span is *recording*, which means docket's
    own server span exists and is being exported. Without an SDK the current
    span is the caller's context with nobody recording it, and storing its span
    id would make the column mean "docket's span" on one row and "whatever the
    caller said" on the next. Null is the honest value: there is no span of
    ours to point at.
    """
    span = trace.get_current_span()
    ctx = span.get_span_context()
    if not ctx.is_valid:
        return None, None
    span_id = trace.format_span_id(ctx.span_id) if span.is_recording() else None
    return trace.format_trace_id(ctx.trace_id), span_id
