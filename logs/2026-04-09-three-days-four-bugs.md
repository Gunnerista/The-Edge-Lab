# Chapter 4 — Three Days, Four Bugs, One Repo Sanitized

**Date:** 2026-04-09  
**TL;DR:** I flipped the system to live capital on April 5. Within four days, I found three bugs in my own pipeline and one bigger problem on this very repo. Here's what each one cost — and what staying ahead of the next one is going to require.

---

## The Setup

By the end of Chapter 3, the model was stable. Entry-price bug fixed, profitable prop types isolated, unprofitable ones disabled, fresh config (`v5_kelly`) deployed with quarter-Kelly sizing and a 5% bankroll cap. The model had 1,283 settled predictions and a clean read on which prop types had real edge.

The plan for the week was simple:

1. Flip the system to **real capital** on rebounds (the only consistently profitable prop)
2. Watch the first live orders settle
3. Backtest the days I had missed during the transition to quantify opportunity cost

That's not what happened. What happened was four bugs in four days, in increasing order of "how did I miss this?"

---

## Layer 1 — Going Live ($66 and a Lot of Nerves)

**Date:** 2026-04-05

The bankroll on day one of live trading was $66.19. Not a typo. The whole point of this phase isn't to make money — it's to prove the system can survive contact with real capital without breaking.

The configuration locked in for live:

| Parameter | Value | Why |
|---|---|---|
| Active prop (live) | Rebounds | Only prop with positive ROI after fees |
| Active props (paper) | Points, Assists | Still on validation |
| Min edge | 15% global, 20% for rebounds | Below 15% the model bleeds to fees |
| Max spread | 15¢ | Wider than the old 10¢ — rebounds markets are thin |
| Sizing | Quarter-Kelly, 5% bankroll cap | Survive variance, not optimize for it |
| YES premium | +5¢ | YES bets on Kalshi are structurally disadvantaged |

The system flipped over at 17:30 ET. Then I sat and watched logs scroll for an hour and went to bed.

---

## Layer 2 — The Missing Zero (and the 88% ROI I Didn't Capture)

**Date:** 2026-04-06 (discovered)  
**Date of damage:** 2026-04-01 through 2026-04-05

The morning after going live, I checked the signal output. Zero signals had been generated for three days straight. Three days. Going into a live deployment.

The cause was one character.

A helper function inside the signal engine constructed Kalshi ticker date patterns from the current date. The line read:

    # wrong
    pattern = f"{d.year % 100:02d}{month_abbr[d.month]}{d.day}"

Kalshi tickers use zero-padded days. April 7th is `26APR07`, not `26APR7`. The pattern `26APR7` matches nothing. Markets exist, the model runs, the filter passes — and then the ticker pattern silently rejects every market because the string never matches.

No exception. No error log. No alert. Just silence.

I had actually fixed this same bug on March 28 in `forward_test.py`. I had not audited the rest of the codebase for the same shape of bug. The fix landed in one file. The disease lived in three more.

The fix:

    # right
    pattern = f"{d.year % 100:02d}{month_abbr[d.month]}{d.day:02d}"

The deeper fix — the one that matters — was extracting it into a single shared utility function (`ticker_date_pattern()` in `shared/tz.py`) and replacing the four inline copies across `signal_engine.py`, `forward_test.py`, `runner.py`, and the MLB equivalent. One implementation. One thing to fix when it breaks again.

After the fix, a single scan cycle recognized 317 markets and generated 6 signals. The system was breathing again.

But I needed to know what those three days had cost.

---

## Layer 3 — First Live Orders (Vucevic, +$1.76)

**Date:** 2026-04-07

The first real-money trades fired the night of April 7 on a Celtics-Hornets game. Two contracts on Nikola Vucevic's rebounds:

| Side | Line | Entry | Size | Edge | Result | P&L |
|---|---|---|---|---|---|---|
| YES | Over 5.5 | 38¢ | 8 contracts | +28.4% | Win | +$4.96 |
| YES | Over 7.5 | 20¢ | 16 contracts | +26.9% | Loss | −$3.20 |
| | | | | | **Net** | **+$1.76** |

Vucevic finished with rebounds in the 6–7 range — over the lower line, under the higher one. Both positions were sized correctly relative to confidence: the larger position on the deeper out-of-the-money line lost, the smaller position on the closer line won.

Net result: a $1.76 gain on $6.24 deployed. As a money event, it's nothing. As a system event, it's the first time the full pipeline — model, sizing, execution, settlement — ran end-to-end on real capital with real outcomes. That mattered more than the dollar figure.

---

## Layer 4 — The Backtest That Hurt to Run

**Date:** 2026-04-08

Now I knew the system worked live. I still didn't know what the missing zero had cost me.

I rebuilt the missed window (April 1–5) from snapshot data and ran the v5 config against it. Two of the days had no usable snapshots. April 4 had 19 markets but zero candidates after the edge filter (the filter doing its job — there genuinely was no edge that day).

April 5 was a different story.

| # | Player | Line | Side | Edge | Entry | Result | P&L |
|---|---|---|---|---|---|---|---|
| 1 | W. Carter Jr. | Over 9.5 | NO | 26.4% | 70¢ | Win | +$1.42 |
| 2 | W. Carter Jr. | Over 5.5 | NO | 68.5% | 25¢ | Win | +$10.14 |
| 3 | W. Carter Jr. | Over 7.5 | NO | 48.2% | 47¢ | Win | +$4.38 |
| 4 | W. Carter Jr. | Over 8.5 | NO | 36.9% | 59¢ | Win | +$2.85 |
| 5 | B. Lopez | Over 3.5 | NO | 28.3% | 34¢ | Win | +$8.25 |
| 6 | B. Lopez | Over 4.5 | NO | 29.0% | 48¢ | Win | +$5.05 |
| 7 | B. Lopez | Over 5.5 | NO | 21.8% | 66¢ | Win | +$2.53 |
| 8 | K. Kuzma | Over 3.5 | NO | 29.7% | 32¢ | Loss | −$5.04 |

**8 signals · 7 wins · 1 loss · +$29.58 · +88.2% ROI**

The bankroll would have grown from $66.19 to $95.78 in one night. Instead it sat unchanged because of one missing `:02d`.

A couple of honest caveats before I get carried away:

- Half the wins came from a single Wendell Carter Jr. game where he barely touched a rebound. That's concentration, not signal quality. One game, four correlated bets, four wins.
- The April 1–3 window had no snapshot data at all, so the actual opportunity cost is *unknowable*, only bounded.
- 8 signals is not a sample size. It's an anecdote with a P&L attached.

But the lesson lands regardless of the sample. **A silent failure that prevents you from acting is the most expensive kind of bug, because you can't even count what it cost you until you go looking.** The system was running. Logs were clean. The model was correct. And the entire pipeline was producing zero signals for three days going into a live deployment.

I will be re-reading my own zero-padding fixes for a while.

---

## Layer 5 — The Audit That Wasn't About the Code

**Date:** 2026-04-09

With the bugs in the system addressed, I turned to the second job on the list: cleaning up this repository. The chapters had drifted out of sync with reality (v5 was running but v4 was the last config the logs mentioned), filenames were inconsistent, and the README was four days stale.

I started by listing every file in the repo. That list is when the real problem showed up.

Sitting in the public repo, indexed by GitHub, readable by anyone with the URL:

- **`CLAUDE.md`** (16KB) — internal config, Platt parameters, database name, file structure, performance numbers
- **`CLAUDE_CODE_MASTER_INSTRUCTIONS.md`** (6KB) — strategic vision plus the name of an older private repo
- **`scripts/`** — 34 files, 722KB. The Kalshi client. The XGBoost feature engineering. The signal engine. The auto-trader. The self-learner. The tuned parameters. The entire system, line by line, a `git clone` away.

The whole point of edge in prediction markets is that you have a view the rest of the market doesn't. The moment that view is published, the rest of the market starts pricing it in. **Alpha decays in the open.** Tony Bloom does not put Starlizard's models on GitHub. Two Sigma does not put their factor library on GitHub. Renaissance does not even let employees take notes home.

I had been treating the repo as a portfolio while accidentally treating it as documentation. Those are two completely different things.

The fix was destructive and necessary:

1. Backup branch created and pushed (then immediately reviewed and removed — pushing the backup defeats the cleanup, which is its own small embarrassment worth recording)
2. `git filter-repo` rewrote history to erase `CLAUDE.md`, `CLAUDE_CODE_MASTER_INSTRUCTIONS.md`, `scripts/`, and `requirements.txt` from every commit they ever appeared in
3. Force-pushed the rewritten history to `origin/main`
4. New `.gitignore` hardened against the same paths reappearing
5. Verified that the deleted paths return 404 from raw GitHub URLs
6. Verified that Google had not yet indexed any of the sensitive content
7. Verified GitHub no longer holds any branches other than `main`

What got kept: the README, the chapter logs, the chapter template, and the directory placeholders. What's public is the **story** of the system. The system itself is not.

Footnote on the philosophy: I am not claiming the code was so clever that the world was about to copy me. The code is fine, not magical. What I am claiming is that the **direction** of my edge — which prop types I trade, what spreads I tolerate, what minimum edge I require, what sizing I use — is the kind of thing that makes its way into other people's order flow if you publish it. Even small amounts of competition collapse thin-market edges fast.

The lesson is not "code is precious." The lesson is **"know which thing you're publishing, and publish only that thing."**

---

## Layer 6 — The Bug Behind the Bug

**Date:** 2026-04-09

While cleaning up the repo, I noticed something in the trade log that didn't add up.

The two Vucevic live trades from April 7 were sitting in `trade_log.jsonl`. They were not in `prediction_log.jsonl`. The trade had executed; the prediction had no record.

Tracing the code paths:

- **`signal_engine.py`** → **`auto_trader.py`** → **`trade_engine.log_trade()`** writes to `trade_log.jsonl`
- **`forward_test.py`** writes to `prediction_log.jsonl` — and only `forward_test.py` writes there

These are two separate code paths that never touch each other. By design, the live scanner skipped the prediction log entirely. The only writer to prediction_log was a 12pm cron job inside `forward_test.py` that batched the day's predictions for evaluation.

That should still have caught April 7's trades, except for the second half of the bug:

> The 12pm cron did not run on April 6, and did not run on April 7. There is no `[Scheduler] Running forward test` line in `runner.log` for either day.

So the actual chain of events was:

1. April 7, 21:59 ET — scanner generates signals, fires Vucevic trades, writes to trade_log only
2. April 8, 06:48 ET — runner restarts, the 12pm cron tries to catch up on April 7 props
3. The catch-up logic checks for `status=active` props for April 7 — finds zero, because the games are already over and the props are now `final`
4. Catch-up logs `"No active NBA props for 2026-04-07, skipping"` and exits
5. The April 7 predictions are now permanently missing from `prediction_log.jsonl`

No exception. No alert. The model had no record that it ever made those calls.

This is the same shape of failure as the zero-padding bug from Layer 2, in a different mask: **the system kept running, the logs looked clean, and a critical write was simply not happening.** Silent failure is the failure mode I am clearly worst at catching, and I need to take that personally.

The structural fix:

- Moved prediction logging into `signal_engine.py` itself, immediately after a signal is appended to the in-memory list. Every signal — dry run or live — now writes to `prediction_log.jsonl` at generation time. The 12pm batch cron is no longer the only writer.
- Added a `logged_today` set to deduplicate when the cron and the scanner both try to write the same ticker on the same day.
- Append failures now write a `logger.warning` instead of being swallowed by a bare `except`. If the writer breaks, I find out the same day instead of three days later.
- The two missing April 7 Vucevic predictions were backfilled with `config_version="v5_kelly_backfill"` and the actual settled outcomes.

After the fix, total settled predictions: **1,660** (was 1,658). The live test at the end of the patch wrote a dry-run signal and a live signal to `prediction_log.jsonl` correctly on the first attempt. A small number, a small win.

**The bug that's still open:** Why did the 12pm scheduler fail to fire on April 6 and April 7? That's a separate bug from the writer gap, and it still hasn't been root-caused. The patch I just deployed makes the prediction log resilient *to* a scheduler failure, but it does not fix the scheduler failure itself. That's the next thing on the queue, and it gets its own chapter when I find it.

---

## Counting the Damage

| Bug | Discovered | Cost | Status |
|---|---|---|---|
| Zero-padding ticker pattern | 2026-04-06 | 3 days of missed signals; bounded ~$29.58 on April 5 alone | Fixed (shared utility) |
| Public IP exposure | 2026-04-09 | Unknown (no fork detected) | Fixed (history rewritten) |
| Prediction log write gap | 2026-04-09 | 2 missing prediction records (backfilled) | Fixed |
| 12pm scheduler silent skip | 2026-04-09 | At least 2 days of cron failures | Open — next chapter |

Three of the four are closed. The fourth is named, scoped, and queued. That's about as honest a summary as I can make right now.

---

## What I'm Taking From This Week

A few things I want to remember next time I'm tempted to ship faster than I audit.

**Silent failure is the most expensive failure.** Every bug I missed this week was silent. The system kept running. The logs kept scrolling. Nothing crashed. The damage compounded behind a green dashboard. I need to add active heartbeat checks — "did the scanner produce at least N signals per day, and if not, why" — instead of trusting "no errors" as a proxy for "system healthy."

**A fix in one file is rarely a fix.** The zero-padding bug had been fixed once already, in a different file, weeks earlier. I did not audit the rest of the codebase for the same shape. A fix that doesn't generalize is a fix that's about to bite you again. Shared utility functions are the only durable answer.

**Know what you're publishing.** A portfolio repo and a documentation repo are not the same thing. I conflated them and I almost gave my edge away for free. The story of a system is publishable. The system itself is not. That distinction is going on the wall above my desk.

**Backtests are for sizing your regret, not your ego.** The April 5 backtest showed an 88% ROI I didn't capture. The temptation is to feel terrible about the missed money. The useful framing is the opposite: the backtest tells me how much sitting silent for three days actually cost, which is the only way to motivate the boring infrastructure work — heartbeats, dedupe, shared utilities — that prevents the next three silent days.

**Write down the embarrassing stuff.** Pushing a backup branch full of the data I was sanitizing was a small, dumb mistake. So was missing the duplicate of a bug I had already fixed. Both went in this chapter on purpose. The point of writing in public is not to look infallible. It's to make the cost of hiding mistakes higher than the cost of fixing them. If I edit out the bad parts, I lose the only thing that makes the good parts believable.

---

## Next

- **Chapter 5 (queued):** Why the 12pm scheduler went silent on April 6 and April 7. Hypotheses, diagnosis, fix.
- **Backlog:** Active heartbeat monitoring (signals/day floor). Audit MLB module for the same write-gap pattern. Expand the rebounds model with opponent pace and defensive rating features.

Status going into next week: live, monitored, and a little more humble than last week.

---

[← Previous: Chapter 3 — The Price Was Wrong](./2026-04-04-the-price-was-wrong.md) | [Next: TBD →]
