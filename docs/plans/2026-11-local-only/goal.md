# Goal: vocalize, local only

Date: 2026-11-21. Owner and decision holder: Mat. Planning and coordination:
Mat's engineering manager agent, on his direct instruction.

## The instruction

Mat, 2026-11-21, word for word:

> I'd like you to fully replan and refactor vocalize to be local only pulling
> on better open source projects where possible. Size my instance to run on
> the studio which has more space for different models. Drop all join meeting
> aspects. It should just be speech to text and text to speach. I want to make
> sure we have a UI for all settings and config choices. No permission needed
> from me for any choices through publication of the new version. Push and
> merge as needed.

Two follow-ups the same day: "Spin up subagents as needed. Make sure you're
choosing appropriate models", and the repository address
`https://github.com/matthager12-collab/vocalize`.

## What that authorizes

- Every design and scope choice up to and including publishing the new
  version, without a further approval round.
- Commits, pushes, pull requests and merges in this repository as needed.
- Delegating work to subagents.

## What it does not change

- Mat's uncommitted edits in the primary checkout stay untouched. All work
  happens in a separate worktree on its own branch.
- Existing branches are left in place. Work that is out of scope is not
  merged; it is not deleted either.
- A third-party package is reviewed for supply-chain risk before it is
  installed or run (the `vet-dependency` procedure). A favourable research
  note is not that review.
- No credential is read, printed or copied by an agent.

## Requirements, as stated

1. **Local only.** Nothing the tool does sends text or audio off the machine.
2. **Better open-source projects where possible.** Prefer a maintained
   open-source project over hand-built machinery when it does the job.
3. **Sized for the Mac Studio.** The owner's instance runs on an M5 Max with
   128 GB of memory and about 1.5 TB of free disk; it can hold several models.
4. **No meeting features.** Nothing that joins, attends or captures a meeting.
5. **Two capabilities only:** speech to text, and text to speech.
6. **A user interface for every setting and configuration choice.**
7. **Published.** The new version ships to the same places the old one did.

## Starting point

- `main` at `7ce02b1` is 0.15.0 plus the table-reading change.
- PyPI package `vocalize-cli` 0.15.0; a public GitHub repository; `main`
  protected by three required test checks.
- Cloud voices (ElevenLabs, OpenAI, Google, Polly), cloud transcript cleanup
  and cloud summaries are in the product today. The default voice chain
  starts with a cloud provider.
- vocalize has never been installed on the Mac Studio.

## Where the evidence goes

This directory: `research/` for the inventories and research notes, then
`design.md`, `plan.md`, `verification.md` and `decisions.md`.
