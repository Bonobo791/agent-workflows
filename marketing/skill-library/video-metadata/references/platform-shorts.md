# YouTube Shorts

Shorts runs on its own ranking lane, separate from long-form: a Short does not lift the channel's long-form, and channel size barely counts on a single Short. Percentages, revenue figures and eligibility numbers below are source-reported (the vyral repo and creator analyses); verify current YouTube rules before advising on money or Partner Program eligibility.

## Contents
1. Principles
2. What the Shorts Feed ranks
3. Seeding
4. Retention: reading the graph, benchmarks, loops, the first 3 seconds
5. Workflow and script shape
6. Short → long-form funnel
7. Search and shelf
8. Monetization and view definitions
9. Anti-patterns
10. Diagnosis order

## 1. Principles
1. Shorts has its own algorithm; each Short is judged on its own.
2. **VVSA (Viewed vs Swiped Away)** is the top-of-funnel gate: under about 60% tends to flop, about 70 to 90% tends to break out, 30/70 is effectively dead. The first 3 seconds decide it.
3. The retention curve is the diagnosis: in Studio, a blue line over a gray band of channel-typical retention.
4. Loops are free retention: a clean loop can push percentage viewed past 100%.
5. The Short → long-form click is the most valuable signal; because the lanes are decoupled it no longer happens by accident, so bridge it explicitly. CTA: "watch the full video on my channel", not "subscribe".
6. Views are inflated (they register on play or loop); Engaged Views are what count for YPP and monetization.
7. Pattern-matching, not prediction.

## 2. What the Shorts Feed ranks (roughly in order)
VVSA → average percentage viewed (about 65% for under-30s, about 50% for 30 to 60s, about 40 to 45% for 60s+) → loop rate (a rewatch within about 2 seconds of the end counts as a partial new view) → shares (heavy weight; they predict reach outside the algorithm) → first-frame engagement → likes and comments (weak for distribution). Viewer satisfaction (post-view surveys) is also an input: a bait-and-switch scores high on watch time and still underperforms.

## 3. Seeding
A new Short starts with a small test audience (source: roughly 50 to 500 viewers, about 70% non-subscribers), so write the opening as if nobody knows the channel. Each upload is one swing of many: nine can die in seeding while one crosses a million. Not rewarded: subscriber count on a single Short, upload frequency as a standalone signal, trending sounds for channels under roughly 50k subs (original audio tends to win), cross-platform watermarks.

## 4. Retention
Graph shapes: **cliff in the first 3 seconds** (weak frame, slow first line, logo or welcome-back, hook not matching title) → rewrite the first 3s entirely; **slow bleed** (no escalation) → add a turn or stakes raise around 5 to 8s; **late cliff before payoff** → move the payoff earlier or tighten setup. Goal: a flat line above the gray band, with a small end bump where loops bring viewers past 100%.
Length: 30 to 45s is the sweet spot for most niches; sub-15s can fail on absolute watch time even at 100% retention; do not stretch to the 3-minute ceiling without a reason (write the shortest Short that fully delivers).
Loop designs: **visual** (last frame matches the first), **audio** (end the voiceover on a beat that hands off to the opening line; no trailing breath or "thanks for watching"), **question** (close on a line that makes the opening hit differently), **cliff** (end on a tease; the payoff is at the start of the loop).
First 3 seconds: a visual that earns the next second (no logo, animated intro or establishing shot); the first spoken line lands inside about 1.5s (cut if it takes over 2); on-screen text on frame 1 in the safe zone (about 70% of Shorts viewing is muted, source-reported).
Re-upload a fixed Short as a new Short; the algorithm grades from upload, not edit history.

## 5. Workflow and script shape
Clarify (niche, goal: reach/subs/long-form clicks/monetization, target length, existing long-form to bridge to, constraints) → pick length and true 9:16 → write the first 3 seconds first → structure for the curve (promise, turn at 5 to 8s, payoff before the natural drop, loop-back) → decide the funnel move → monetization pass → anti-pattern sweep → close honestly (watch VVSA and the curve, not raw views).
Script skeleton (30 to 45s): 0 to 3s HOOK (payoff or stake, 3 to 5 bold words, no intro) · 3 to 8s ESCALATION 1 (turn, stakes raise, specificity) · 8 to about 25s ESCALATION 2 ("but/therefore") · about 25 to 38s PAYOFF (show the result) · last 5s LONG-FORM CALLOUT naming the video, or a hold/loop frame. Fields to fill: hook archetype, goal, long-form to bridge to, loop design, original audio?, aspect ratio, captions burned in and in the safe zone, promise/payoff match. Template: `assets/short-script-template.md`.

## 6. Short → long-form funnel
- Source-reported economics: a 1M-view Short adds roughly 500 to 5,000 subscribers vs 20,000 to 50,000 for a 1M-view long-form; Shorts RPM about $0.03 to $0.10 per 1,000 views vs long-form about $3 to $15. A Shorts-only subscriber who never clicks through is worth little.
- Four bridge surfaces; fire at least three on the same Short: (1) **in-video callout** in the last 5s naming the destination ("the 12-minute version is the [exact title] video on my channel"); (2) **pinned comment** with the link plus "one specific extra not in the Short" ("every product I used, with timestamps": "same but longer" gets no clicks); (3) **end card / final label** ("Full video: [title]"), pairing with the loop; (4) **channel trailer and Shorts tab consistency**: batch Shorts by topic, pin 2 to 3 strong Shorts that match the long-form library, avoid off-topic uploads for a day or two after a Short that may pop.
- No long-form exists? Test: if the topic is worth a long-form, write the Short as a teaser and ship the long-form first; if it is a one-off, skip the funnel CTA and let it earn reach.
- Measure per Short in Studio (traffic source "From a Short"); one or two Shorts usually send most of the clicks. Template: `assets/short-to-longform-bridge.md`.

## 7. Search and shelf
- Surfaces: the vertical feed; the home-feed **Shorts shelf** (source: roughly 80% of home-feed slots after the redesign); the channel **Shorts tab**; **search** with a Shorts filter.
- Shelf: the first frame is shown as a still, so it must hold frozen (clean contrast, no blur, not text-heavy); titles read like search-intent matches.
- Search indexes title, description, spoken transcript and on-screen text (OCR): write the title like a query ("How to wash a cast-iron skillet"), a 2 to 3 sentence description, say the keywords aloud, burn them into on-screen text. Search-driven Shorts are evergreen and accumulate over months.
- Pick one primary surface per Short (feed = VVSA and loops; shelf = first-frame quality and consistency; search = title, transcript and OCR). Tags barely matter; custom thumbnails do not show in the feed (the first frame does); long-form cards and end screens do not appear on Shorts.

## 8. Monetization and view definitions
- **Engaged Views** (meaningful watch time) count for YPP and revenue; plain Views do not. Check the split in Studio before calling a Short a hit.
- Creator Pool (source): monthly Shorts ad revenue is split between a music-licensing bucket and the Creator Pool (weighted by tracks used), allocated by share of Engaged Views, and the creator receives 45% of their allocation. One licensed track can cost roughly half a Short's slice; original audio keeps it. A Short over 60 seconds with an active Content ID claim is blocked globally and earns nothing (Shorts-only rule).
- YPP via Shorts (source-reported): 1,000 subscribers plus 10 million valid public Shorts Engaged Views in 90 days; long-form alternative: 1,000 subscribers plus 4,000 public watch hours in 12 months. Verify before quoting.
- Modes: monetization (original audio, 30 to 60s, clear bridge) vs reach (15 to 30s, VVSA and loops, trending audio only above about 50k subs); mixing both on one Short tends to fail both.

## 9. Anti-patterns
TikTok watermark (demoted); 16:9 with bars (classified as long-form and never enters the feed); "Subscribe!" CTA (pulls retention down; accidental taps already produce subs); welcome-back, logo or animated intros; trending sound on a small channel; captions outside the safe zone (top about 20% and bottom about 25% are UI; keep text in the middle third, roughly 888 x 1500 of 1080 x 1920) or relying on auto-subtitles that show below the player; mass-produced or repetitious content (low-effort AI slideshows, near-duplicate Shorts, stock-footage voiceover spam; a creator-written voiceover over original visuals passes); off-topic Shorts tab after a pop; confusing Views with Engaged Views; stretching to the length ceiling.

## 10. Diagnosis order
1. Watermark · 2. Aspect ratio · 3. First 3 seconds (intro, logo, hook delay) · 4. Caption safe zone · 5. CTA (subscribe vs bridge) · 6. Music (licensed track on a small channel) · 7. Views vs Engaged Views.
Then: VVSA under 60% = opening failed, rewrite from frame one; healthy VVSA but percentage viewed under the length benchmark = body bleeding; percentage viewed over 100% = loops firing, so look at shares, topic ceiling or anti-patterns; zero long-form clicks on a Short that performed = the funnel is missing, not the video. Always read VVSA first.
Pre-upload checklist: `assets/short-checklist.md`; script skeleton `assets/short-script-template.md`; bridge worksheet `assets/short-to-longform-bridge.md`.
