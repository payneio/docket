# Working here

This directory belongs to one piece of work. Something was offered to you, you
took it on, and this is where you do it. The directory persists: if you are
asked about this again next week, you will be back here with whatever you left.

**You do not need to know anything about the register that sent you.** You will
not be asked to poll it, read it, or report to it. Something else does that, and
it does it from what you write below.

## Say what happened before you stop

Write `.harness/outcome.json` before you finish:

```json
{
  "outcome": "delivered",
  "note": "One line. This is what a person reads later."
}
```

`outcome` is one of:

| | when |
|---|---|
| `delivered` | you believe the thing that was asked for is done |
| `progressed` | you did some of it and it is not finished |
| `blocked` | you cannot go further without an answer from somebody |
| `declined` | this is not yours to do, or you should not be the one doing it |
| `failed` | you could not work — a broken tool, a missing dependency |

When you are `blocked`, add the question:

```json
{
  "outcome": "blocked",
  "note": "Cannot pick between two schemas without knowing the retention rule.",
  "question": "Should old rows be deleted or archived after 90 days?"
}
```

That question is **opened as a real request to the person who asked you**, and
stays outstanding until they answer it. It is not a note in a log. So ask it the
way you would ask a colleague: one specific thing you need, not a summary of
your confusion.

## Why this matters more than it looks

Without the file, whatever ran you has to guess your outcome by reading your
prose for words like "blocked". That works until it doesn't, and when it fails
it fails silently in the expensive direction — work recorded as progressing
while it is stuck, or as finished while it is not.

Two things follow:

- **`declined` is a real answer and you are allowed to give it.** If this was
  offered to you wrongly, say so. Refusing is not failing. What must not happen
  is that you attempt something out of scope because refusing was not an option
  you were offered — the whole arrangement exists so that a refusal is sayable.

- **Do not report progress you have not made.** Saying `progressed` every run
  while nothing changes looks like attention and is worse than silence, because
  it hides how long the work has actually been stuck. If you did nothing, say
  what stopped you.

## Do not

- Poll or write to any task system yourself. You will duplicate what is already
  being recorded, and the two will disagree.
- Loop. Reach a stable point and stop. If you need something, ask for it and
  stop; you will be run again when there is an answer.
- Work outside this directory unless what was asked plainly requires it.
