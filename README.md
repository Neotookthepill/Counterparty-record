# The Record

> Turn a live markets show into a searchable, teachable record.

**The Record** is an independent, source-linked intelligence layer developed around Counterparty and The Threadguy Stream. It preserves the useful parts of long-form live markets conversations: the exact words, the context, the changing views, and the source moment where each statement was made.

It is designed to make a live show useful after it ends — not as a generic transcript dump, call leaderboard, or generic crypto dashboard.

**Live concept:** [counterparty.netlify.app](https://counterparty.netlify.app/)

> This is an independent editorial proof of concept, built for Counterparty. It is not an official Counterparty product.

## What it does

- **Archive / Tape** — search timestamped show moments and return to the original source.
- **Ledger** — preserve market statements while distinguishing explicit positions, market reads, and retrospective claims.
- **Asset dossiers** — follow a recorded asset conversation through time without inventing exits, P&L, or portfolio exposure.
- **Academy** — teach how a real setup was framed, tested, invalidated, or updated using sourced show moments.
- **Lexicon** — explain the show’s own language in plain English, rather than sending viewers elsewhere to learn it.
- **Dispatches** — turn selected episodes into readable editorial editions.
- **The Desk** — the future independent research surface for crypto, equities, low-cap/on-chain work, and follow-up analysis.

## The product idea

Long-form market shows generate calls, frameworks, guest insights, jokes, narrative shifts, and changing convictions every day. Most of that value disappears into a three-hour replay.

The Record turns it into durable intellectual property:

| A live show creates | The Record preserves |
| --- | --- |
| A conversation | A searchable source record |
| A call | Its wording, context, timestamp, and source |
| A framework | A lesson somebody can revisit |
| A changing view | A visible history rather than revisionism |

The goal is not to score traders or pretend a transcript can replace editorial judgment. The goal is to help viewers **catch up, understand, follow, and verify**.

## Evidence first

The system follows a deliberately conservative rule set:

- **Attribute or abstain.** A guest statement is not silently attributed to the host.
- **Verify or qualify.** A source link is evidence; it is not a blanket claim that every inference is proven.
- **Separate types of statements.** A disclosed position is not the same thing as a market read, scenario, or retrospective claim.
- **Never invent an exit or P&L.** The Record logs what was expressed on air and links back to it.
- **Use context, not verdicts.** Prices and later market context may inform a lesson, but never rewrite the original statement.

## Current status

The project is an early working system and an editorial proof of capability. It contains real show data and source-linked interactions, alongside surfaces that are still intentionally editorial or in progress.

### Already demonstrated

- Transcript/archive search
- Source-linked moments
- Structured market-statement records
- Dossier and chronology concepts
- Academy case-study and Lexicon formats
- Counterparty-aligned editorial visual system

### Next

- Establish a human-labeled evaluation set for extraction accuracy
- Expand the evidence model into exclusive **Mention / Read / Position** collections
- Refresh the ingestion pipeline safely and automate updates only after end-to-end validation
- Connect Academy studies more directly to the live record
- Publish original, clearly labeled independent work through The Desk

## Local development

```bash
npm install
npm run dev
```

Useful checks:

```bash
npm run build
npm run lint
npm test
```

The local data snapshot lives in `public/the_record_data.json`. Treat it as a snapshot: do not claim its counts, dates, or coverage are live unless a validated ingestion run has refreshed it.

## Project structure

```text
app/
  knowledge-engine/       # Main Counterparty intelligence-layer concept
  directions/             # Layout and product directions
  layout-alternatives/    # Alternative visual systems
  proposal/               # Proposal surface
  radical-variants/       # Exploratory visual variants
public/
  the_record_data.json    # Local show-data snapshot
```

## Built by

Kenny — independent editorial, degen/on-chain analysis, and product direction.

The ambition is to operate this as a living intelligence layer around the show: research before the stream, preserve and teach what matters afterward, and extend the conversation between broadcasts.
