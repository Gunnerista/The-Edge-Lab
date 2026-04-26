# Chapter 5 — Closing the Books on Phase One

**Date:** 2026-04-26
**TL;DR:** The model on rebounds had real edge. The pipeline around it didn't deliver
that edge cleanly enough to scale. Phase One closes without a published P&L number — by
choice. Here's the reasoning.

---

## What I Set Out to Do

Phase One started on a slow Tuesday. I had been playing around on Kalshi with friends,
mostly NBA props for fun, and started wondering whether the prices were actually right.
The whole project ran from "could I vibe code something to figure that out" to "thirty
days of live capital, here's what happened" — and this is where it lands.

The technical question was simple enough: given a calibrated XGBoost model on NBA
player props, plus quarter-Kelly sizing, plus a live Kalshi API connection — does that
combination clear fees and variance on real capital, on a small slate of games per
night, over a roughly thirty-day window?

The answer turned out to be more interesting than yes or no.

---

## What Worked

**The model.** XGBoost on box-score features, calibrated with Platt scaling refit on
settled forward-test data, produced Brier scores in the 0.16–0.17 range across 1,660+
settled predictions. That is genuinely good probability estimation for this domain.
Chapters 1 through 3 cover the construction.

**Rebounds, specifically.** When the entry-price bug from Chapter 3 was fixed and ROI
was recomputed correctly, rebounds came in at +17.6% across a clean window. Points and
assists did not. Phase One went live with rebounds-only on real capital and held the
others in paper validation.

**Quarter-fraction Kelly with a hard bankroll cap.** The sizing rule that survived from
Day 1 to the close: 0.25-fraction Kelly, 5% maximum single-position size, fixed minimum
bet floor. Earlier configs with adaptive multipliers blew up on the first bad night.
Quarter-fraction did not.

**The infrastructure transition.** SQLite to PostgreSQL early on (Chapter 2) was the
most important non-model decision. The single-writer lock was incompatible with three
concurrent threads. The migration paid for itself many times over.

---

## What Didn't

**The settlement pipeline drifted out of sync with reality.** Between zero-padding bugs,
scheduler skips, and a write-gap between trade execution and prediction logging, the
system accumulated a backlog of unsettled records. When the settler finally caught up
in mid-April, it processed everything in batch, and the internal bankroll accumulator
decided the system had gone bankrupt — recording a negative five-figure balance. The
real Kalshi balance on the same day was up. That gap, between what the safety code
thought was happening and what actually was, is the most expensive bug in the project.

**Multiple sources of truth fought each other.** `paper_trades.jsonl`,
`prediction_log.jsonl`, `safety_state.json`, `equity_curve.json`, the live Kalshi API
balance — five places that should have agreed on basic facts and didn't, consistently.
Reconciling them after the fact was not always possible.

**Silent failures were everything.** Almost every bug in this project ran for days
before it was noticed. Zero exceptions, clean log files, no alerts — and a critical
write was simply not happening. The bugs that hurt this project were never the ones
that crashed.

---

## Why I'm Not Publishing a Bottom-Line P&L

This is the part that matters for anyone reading this as a portfolio piece, so I want
to be precise about it.

A clean cash-flow snapshot of the Kalshi account would produce one number. That number,
taken in isolation, would either flatter or condemn the system inaccurately, because:

- It would not distinguish between gains attributable to the model's edge on rebounds
  and gains attributable to variance over a small sample of live trades.
- It would not account for activity outside the automated system that touched the same
  account during the same window.
- It would not adjust for the settlement gaps and accounting drift described above.

Publishing one summary figure, in either direction, would require glossing over those
distinctions. I have decided not to do that. The version of the number that would fit
on a single line of a portfolio README is not a number I am willing to put my name
behind.

What I am willing to put my name behind: the chapters, the errata, the technical
decisions, the failure modes, and the lesson summary that follows. Those are the
project's real output.

---

## What I'd Do Differently

If there is a Phase Two — and that decision is open — these are the changes that would
happen first, before any capital deployment.

**One ledger, one source of truth.** No reconciliation across `safety_state.json`,
`equity_curve.json`, `paper_trades.jsonl`, and the Kalshi API. A single canonical record
of every position, with everything else as a derived view.

**Active heartbeat monitoring.** "Did the system produce *N* outputs today? If not,
why?" — running independently from the system itself. Silent failure is the failure
mode I am clearly worst at catching.

**Stricter paper / live separation.** The same code path running in two modes, with the
only difference being whether orders go to Kalshi, was a source of more bugs than it
saved on duplicated logic. A real interface boundary.

**Smaller scope, longer test window.** Phase One ran thirty days on rebounds-only.
Phase Two, if it happens, runs sixty-plus days on rebounds-only — single prop, single
side preference, no expansion until the boring infrastructure has held without
intervention for at least a month.

**Independent settlement reconciliation.** Compare local settlement records against
Kalshi's portfolio API at end-of-day, every day, with a hard-fail alert on any
mismatch. The bankruptcy event would have been caught immediately under this rule.

---

## Project Status

The runner is shut off. Task Scheduler is disabled. The Kalshi API key for this account
has been revoked. The repository will remain public as a record of what was attempted
and what was learned. Whether there is a Phase Two is undecided.

What survives this project, regardless of whether there is a sequel:

- A working XGBoost + Platt-scaled probability model for NBA player rebounds
- A documented record of every config version (`legacy` → `v2_platt_edge20` →
  `v3_prop_edge` → `v4_edge_optimized` → `v5_kelly`) with the reasoning for each
  transition
- A complete catalog of the failure modes encountered (zero-padding, scheduler skips,
  write gaps, accounting drift) and the structural fixes that closed each one
- Five chapters of public-record, real-time engineering documentation

For a one-month side project on an automated trading system, that is the actual
deliverable. It is not the deliverable a recruiter would want me to summarize as
"+X%." It is the deliverable I am willing to defend as honest.

---

## Closing

Phase One closes. The model worked, the pipeline didn't quite, the bottom-line number
stays unpublished by choice, and the lessons compound for whatever comes next.

Status going into the offseason: paused, instrumented, and a little wiser than thirty
days ago.

---

[← Previous: Chapter 4 — Three Days, Four Bugs, One Repo Sanitized](./2026-04-09-three-days-four-bugs.md)
