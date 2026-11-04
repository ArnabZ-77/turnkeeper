# YouTube upload

- **File:** `video/out/turnkeeper_v3_master.mp4` (2:57.0, 1920x1080)
- **Thumbnail:** `video/out/thumbnail.png` (1280x720, about 0.5 MB, under YouTube's 2 MB limit)
- **Visibility:** Public (the hackathon rules require a public YouTube video under 3:00)
- **Captions:** upload `video/out/turnkeeper_v3.srt` as English subtitles (50 cues; the last, "Your circle, kept.",
  starts at 2:53.8). Spot-check one cue in the middle before publishing.
- **Settings:** not made for kids; under "Altered or synthetic content", answer **yes** (the narration is synthetic
  speech). No music is used.

## Title (57 characters)

```
Turnkeeper: An AI Treasurer for Savings Circles on PayPal
```

## Description

Paste as is. YouTube rejects angle brackets, so there are none. If you have the Devpost link, add it as a third link
line.

```
Ravi's payment just failed. Priya, the organizer, didn't chase him: her AI treasurer (Gemini, replayed from a recorded run) worked out a fix the circle's own rules allow, every member's agent voted inside a signed mandate, and PayPal paid out the pot.
Live demo: https://turnkeeper-s8xv.onrender.com · Code (MIT): https://github.com/ArnabZ-77/turnkeeper

Turnkeeper is an AI treasurer for rotating savings circles (chit, susu, tanda, chama, stokvel), built for the PayPal AI Hackathon 2026. Members approve once on PayPal (Vault). Every round is collected with merchant-initiated charges (Orders), each confirmed through the PayPal Agent Toolkit. A shortfall becomes the remedies the charter allows: a buffer advance, repayment by PayPal invoice with a QR code, a turn swap. The pot goes out through PayPal Payouts after the circle's ledger checks out. The model proposes, a policy engine decides, and people and member agents act inside signed mandates. A charge outside any member's mandate is refused before PayPal is called.

Chapters
0:00 Ravi's payment fails, and the organizer's problem
0:20 Rules become a charter; members approve on PayPal and hold signed mandates
0:39 Forecast flags Ravi; Round 3 runs live: five charges, one decline
0:56 Ravi replies in Hindi; Gemini proposes charter-legal fixes
1:15 An injection test, then member agents vote inside their mandates
1:31 Ravi's own AI assistant answers over MCP, for him only
1:41 Board reschedule, invoice with QR, every member's passbook
1:53 Payout read back from PayPal; a $230 charge refused; clean Round 4
2:14 How it's built: PayPal APIs, MCP, a free Gemini model
2:28 Evidence: held-out eval and the live sandbox run
2:40 Prior art and what we don't claim

Try it yourself: open the demo and press the Judge Mode button. Add ?member_agents=1 to the URL to see the member agents. The free host can take about a minute to wake.

Mandates are AP2-style, our own implementation (PayPal AP2 APIs are not public); signatures are server-attested, not keys held by members. Priya is a composite persona.

Sandbox only; AI narration is text-to-speech; no real money.
```

## Tags

```
PayPal, PayPal AI Hackathon, PayPal sandbox, PayPal Agent Toolkit, PayPal Payouts, PayPal Vault, AI agent, agentic commerce, savings circle, ROSCA, chit fund, susu, tanda, stokvel, community finance, MCP, Model Context Protocol, Bryntum Scheduler, FastAPI, React, Devpost, hackathon demo
```

## Where the chapter times come from

Scene start times in `video/script/storyboard.json` (v3, `total_sec` 176.914; `ffprobe` reads the master as 177.0 s).
Each chapter starts at the next whole second after its first scene begins, and every chapter is at least 10 seconds
long, as YouTube requires.

| Chapter | Scenes | Scene start (s) |
|---|---|---|
| 0:00 | s00 cold open, s02 Priya and figures | 0.0, 6.4 |
| 0:20 | s04 compile, s05 phone join, s05b mandates | 19.0, 26.6, 32.0 |
| 0:39 | s06 forecast, s07 round 3 | 38.3, 45.5 |
| 0:56 | s08 Ravi's reply, s09 remedies (recorded Gemini) | 55.0, 64.3 |
| 1:15 | s09b injection test, s10 member agents | 74.4, 82.2 |
| 1:31 | s10b Ravi's own assistant (MCP transcript) | 90.2 |
| 1:41 | s11 execute, s11b passbooks | 99.9, 104.9 |
| 1:53 | s12 payout, s12b PayPal read-back, s13a $230 refusal, s13 round 4 | 111.9, 118.5, 121.5, 128.2 |
| 2:14 | s14 how it's built | 133.2 |
| 2:28 | s17 evidence | 147.9 |
| 2:40 | s18 prior art and honesty, s19 outro | 159.0, 172.6 |
