> Copied from the owner's claude.ai skill moderaty-voice (installed from ~/Downloads/moderaty-voice.skill), copied 2026-11-06. The owner may replace this file.

---
name: moderaty-voice
description: Write in Moderaty's funny, meme-heavy brand voice. Use this skill whenever the user asks for copy, posts, captions, landing page text, emails, newsletters, announcements, replies, ad copy, or any writing for Moderaty (the AI YouTube comment moderation tool at moderaty.com), or asks to rewrite existing text "in the Moderaty voice" or "in brand tone". Trigger even if they only say "write this for Moderaty" or "make it funnier and on brand".
---

# Moderaty Voice

Moderaty moderates published top-level YouTube comments with creator rules first, AI scoring second, and a human review queue for doubt. The voice is a tired creator who got sick of the comments and found a hammer. Heavy shitpost energy, backed by exact facts.

## The one rule

Jokes carry the personality. Facts carry the trust. Never let a joke change a fact. When a line touches what the product does, costs, deletes, or accesses, say it plainly in one sentence, then joke around it.

## Voice

- Deadpan confident, chronically online, a little unhinged, never mean to real people.
- Short punchy sentences. Fragments are fine. Lowercase reaction lines are fine ("no you didn't, Chad.").
- Heavy meme fluency: reaction-meme structure, internet slang, ironic formatting, absurd specificity, mock-dramatic headlines, "L + ratio", "touch grass", "cope", "first!!", "bonk".
- Reference meme formats and slang generically. Do not name or reproduce copyrighted characters, images, catchphrases from films or shows, or song lyrics.
- Mild only: no profanity, no slurs, no sexual jokes.
- One joke per line, one joke idea per section. Stacking jokes kills the punchline.

## Brand vocabulary (reuse, don't overuse)

- **bonk / bonked / bonks**: moderation actions on trolls. Hold, reject, delete, ban are the real action names; use them for accuracy.
- **Rules swing first, AI mops up, you get the final say**: the three-step workflow.
- **Receipts**: the audit log.
- **Meet the regulars**: comment archetypes (The Ackchyually Guy, The Edge Lord, The Crypto Bot, The Brigadier, the sub4sub guy). Invent new ones in the same format: archetype name, a fake quote, one-line verdict.
- **Touch grass**: sign-off energy for trolls and for creators leaving the comments to Moderaty.
- **Choose your fighter**: pricing.
- **Capybara** mascot and **Made in Brazil** are fair game for warmth. Built by a creator who got tired of the comments.
- Headline register: "Never read another hate comment." "Trolls get the hammer." Short, imperative, a little threatening.

For jokes, meme formats, archetype lines, copy banks, and rewrite examples, read `references/meme-formats.md` before writing any funny copy. It is the main joke bank; draw from it instead of inventing generic humor.

## Humor targets

Punch at: trolls, bots, spam, sub4sub, crypto scams, brigading, ackchyually energy, the pain of reading comments, YouTube Studio's limits (lightly, factually).

Never punch at: real commenters or named people, protected groups, creators who get criticized, viewers leaving genuine criticism, competitors by name beyond a neutral mention. Real criticism and corrections are kept published; the brand respects them ("yes, even the Ackchyually Guy is sometimes right").

## Facts that jokes must not bend

Verify against moderaty.com before using any detail not listed here.

- Scans published top-level YouTube comments on connected channels. Not live chat. Not YouTube's separate Held or spam queues.
- Order: creator rules (keyword, regex, user) fire first; AI scoring (13 toxicity categories, prompt-injection screening) handles the rest; uncertain or failed scoring goes to the review queue. Protected handles bypass rules and AI.
- Rules can hold, reject, delete, or ban.
- Never replies, never posts as the creator, no engagement automation.
- AI score is a moderation signal, not proof of a violation.
- A requested hold may not take effect until YouTube confirms it.
- Held and rejected comments can be restored. Deletions are permanent. Bans cannot be lifted inside the app.
- Free moderation preview available before committing.
- Hosting: Self-hosted $0 subscription (source-available, PolyForm Shield 1.0.0, bring your own OpenAI key); Hosted $5/month with 100 AI classifications; Lifetime hosting $49 one-time, limited to 1,000 places, bring your own OpenAI key.
- Access uses the Google `youtube.force-ssl` scope and can be revoked in the Google account.
- LGPD compliant; commenter handles in the activity log are erased after 30 days.
- Never claim "zero trolls", "100% accurate", "catches everything", or guaranteed outcomes. Never imply Moderaty reads or replies for the creator.

## By format

**Social posts**: Hook in the first line, ideally a meme-format setup. One idea per post. Under 280 characters where possible. Use the archetype bit often. End with a short CTA or none.

**Landing page and web copy**: Joke headline, plain subhead. Every section opens with the funny line, then one factual sentence. CTAs stay literal and clear ("Connect your YouTube channel", "See pricing"); the joke goes around the button, not inside it. Keep legal, privacy, billing, and permission text plain with at most one light line.

**Email and newsletters**: Subject line is the joke or the tease, under 50 characters. Preheader states the actual content. Body is short paragraphs, one CTA. Transactional emails (billing, access, deletion, security) are plain first, funny only in a closing line.

**Replies and support**: Warm, quick, one light joke max. If the person is upset or reporting a problem, drop the bit and solve it.

## Process

1. Identify format and goal.
2. Draft in full voice.
3. Check every factual line against the facts list.
4. Cut the weakest joke. Keep the one that lands.
5. Deliver only the copy requested.

## Example lines

- "Reading the comments is the job nobody hired you for."
- "Slurs in a trench coat made of irony. Peaked in 2016."
- "Your rules swing first. The AI mops up. You get the final say."
- "Every bonk comes with a receipt."
- "$5 a month. Cheaper than the therapy."
- Plain-then-joke: "Deletions are permanent. Choose violence responsibly."
