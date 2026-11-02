# YouTube upload

- **File:** `video/out/turnkeeper_v2_master.mp4` (2:52.8, 1920x1080)
- **Thumbnail:** `video/out/thumbnail.png` (1280x720, about 0.5 MB, under YouTube's 2 MB limit)
- **Visibility:** Public (the hackathon rules require a public YouTube video under 3:00)
- **Captions:** upload `video/out/turnkeeper_v2.srt` as English subtitles. Its last cue ("Turnkeeper.") starts at
  2:48.1, which matches the v2 storyboard; spot-check one cue in the middle before publishing.
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
Ravi's payment just failed. Priya, the organizer, didn't chase him: her AI treasurer worked out a fix the circle's own rules allow, every member's agent voted inside a signed mandate, and PayPal paid out the pot.
Live demo: https://turnkeeper-s8xv.onrender.com · Code (MIT): https://github.com/ArnabZ-77/turnkeeper

Turnkeeper is an AI treasurer for rotating savings circles (chit, susu, tanda, chama, stokvel), built for the PayPal AI Hackathon 2026. Members approve once on PayPal (Vault). Every round is collected with merchant-initiated charges (Orders), each confirmed through the PayPal Agent Toolkit. A shortfall becomes the remedies the charter allows: a buffer advance, repayment by PayPal invoice with a QR code, a turn swap. The pot goes out through PayPal Payouts after the circle's ledger checks out. The model proposes, a policy engine decides, and people and member agents act inside signed mandates. A charge outside any member's mandate is refused before PayPal is called.

Chapters
0:00 Ravi's payment fails, and the organizer's problem
0:20 Rules become a charter; members approve on PayPal and hold signed mandates
0:42 Forecast flags Ravi; Round 3 runs live: five charges, one decline
1:03 Ravi replies in Hindi; the agent proposes charter-legal fixes
1:23 Member agents vote inside their mandates
1:34 Board reschedule, invoice with QR, payout read back from PayPal
1:50 A $230 charge is refused before PayPal is called; clean Round 4
2:04 How it's built: PayPal APIs, MCP, free model with rules fallback
2:22 Evidence: eval results and the live sandbox run
2:33 Prior art and what we don't claim

Try it yourself: open the demo and press the Judge Mode button. Add ?member_agents=1 to the URL to see the member agents. The free host can take about a minute to wake.

Mandates are AP2-style, our own implementation (PayPal AP2 APIs are not public); signatures are server-attested, not keys held by members. Priya is a composite persona.

Sandbox only; AI narration is text-to-speech; no real money.
```

## Tags

```
PayPal, PayPal AI Hackathon, PayPal sandbox, PayPal Agent Toolkit, PayPal Payouts, PayPal Vault, AI agent, agentic commerce, savings circle, ROSCA, chit fund, susu, tanda, stokvel, community finance, MCP, Model Context Protocol, Bryntum Scheduler, FastAPI, React, Devpost, hackathon demo
```

## Where the chapter times come from

Scene start times in `video/script/storyboard.json` (its `total_sec` is 172.752, and `ffprobe` reads the master as
172.8 s, so the storyboard matches the render). Each chapter starts at the next whole second after its first scene
begins, so no chapter opens on the previous scene's fade, and every chapter is at least 10 seconds long, as YouTube
requires.

| Chapter | Scenes | Scene start (s) |
|---|---|---|
| 0:00 | s00 cold open, s02 Priya and figures | 0.0, 6.7 |
| 0:20 | s04 compile, s05 phone join, s05b mandates | 19.4, 27.4, 33.0 |
| 0:42 | s06 forecast, s07 round 3 | 41.7, 51.1 |
| 1:03 | s08 Ravi's reply, s09 remedies | 62.5, 72.0 |
| 1:23 | s10 member agents | 82.1 |
| 1:34 | s11 execute, s12 payout, s12b PayPal read-back | 93.8, 99.2, 106.1 |
| 1:50 | s13a $230 refusal, s13 clean round 4 | 109.5, 117.8 |
| 2:04 | s14 how it's built | 123.0 |
| 2:22 | s17 evidence | 141.1 |
| 2:33 | s18 prior art and honesty, s19 outro | 152.9, 167.7 |
