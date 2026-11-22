# Goal: vocalize, local only

Date: 2026-11-21. The owner is the decision holder. The owner approved
every phase of this plan on 2026-11-21, up to and including publishing the
new version.

## What the release must do

1. **Local only.** Nothing the tool does sends text or audio off the
   machine.
2. **Better open-source projects where possible.** A maintained
   open-source project is preferred over hand-built machinery when it does
   the job.
3. **Sized for the Mac Studio.** The owner's copy runs on an M5 Max with
   128 GB of memory and about 1.5 TB of free disk, so it can hold several
   models.
4. **Nothing that joins or records calls.** No feature joins, attends or
   captures an online call or conference.
5. **Two capabilities only:** speech to text, and text to speech.
6. **A user interface for every setting and configuration choice.**
7. **Published.** The new version ships to the same places the old one
   did.

## What the approval covers

- Every design and scope choice up to and including publishing the new
  version, without a further approval round.
- Commits, pushes, pull requests and merges in this repository as needed.

## Out of scope

- The owner's uncommitted edits in the primary checkout stay untouched.
  All work happens in a separate worktree on its own branch.
- Existing branches are left in place. Work that is out of scope is not
  merged; it is not deleted either.
- A third-party package is reviewed for supply-chain risk before it is
  installed or run. A favourable research note is not that review.
- No credential is read, printed or copied during the work.
- Cloud voices and hosted models: users who need them stay on 0.15.

## How done is judged

- Each phase in [plan.md](./plan.md) has exit criteria, checked by
  `verify.sh` and listed in [verification.md](./verification.md).
- The engines are measured on the Mac Studio against the thresholds in
  verification.md, and the results are written in
  [measurements.md](./measurements.md).
- A reviewer who wrote none of the code reviews the whole change, and no
  critical or high finding is left open before publishing.

## Starting point

- `main` at `7ce02b1` is 0.15.0 plus the table-reading change.
- PyPI package `vocalize-cli` 0.15.0; a public GitHub repository; `main`
  protected by three required test checks.
- Cloud voices (four hosted services), cloud transcript cleanup and cloud
  summaries are in the product today. The default voice chain starts with
  a cloud provider.
- vocalize has never been installed on the Mac Studio.

## Where the evidence goes

This directory: [design.md](./design.md), [plan.md](./plan.md),
[verification.md](./verification.md), [decisions.md](./decisions.md) and
[measurements.md](./measurements.md).
