# Attribution and scope

This skill merges content from the seven MIT-licensed repositories below. Each repository's own LICENSE is MIT; the notice text is reproduced at the bottom. Scripts are copied from the sources (with path fixes so they run from one folder). Reference files are rewritten syntheses, not verbatim copies. Templates in `assets/` are copied from the sources with promotional text removed.

Selection rule: repositories whose latest commit falls inside the six months before 7 November 2026 (so 7 May to 7 November 2026).

## Sources used

| Repository | Last commit | Copyright holder(s) | Used for |
|---|---|---|---|
| sergebulaev/youtube-skills | 2026-10-27 | Sergey Bulaev | title formulas and checklist, hook beats, thumbnail principles and brief template, description template, ranking heuristics, voice/AI-tell rules, untrusted-content rule, channel optimizer, community posts, audience insights, content planner, repurposer |
| mohitagw15856/pm-claude-skills (youtube-script and youtube-script-writer only) | 2026-11-02 | Mohit Aggarwal | long-form script structure, output format, quality checks, anti-patterns |
| ahmedsakri/youtube-agent-pro | 2026-10-09 | Ahmed Sakri, Jake Schincariol | scripts (hook, title, idea, thumbnail, funnel, retention, chapters, edit, voice, sponsor, outlier research), analytics, audit, SEO metadata rules, brief, series, trend, end screens, collab, voice template |
| adityaarsharma/youtube-marketing-skills | 2026-10-10 | Aditya Sharma | hook variants, retention scripting guide, thumbnail variants and clarity check, Shorts playbook, funnel, monetization, collab, comment mining, batch re-optimization, competitor analysis, calendar, channel-type templates |
| Jakeschincariol/youtube-agent-skill | 2026-09-16 | Jake Schincariol | the 21 hook formulas (`hooks.json`), original scripts, skill structure |
| vyralcontent/content-skills | 2026-06-22 | Vyral | short-form core, hook archetypes and anti-patterns, TikTok, Reels, Shorts, captions and CTAs, ideation, repurposing, templates |
| iart-ai/tiktok-video-skills | 2026-06-22 | iart.ai | short-form retention grammar, safe zones, loops, caption placement |

## Left out on purpose
- Publishing and integration code (Publora and Pixfaro clients, DataForSEO and WordPress calls, Apify/MCP wiring, YouTube Data API clients): this pack drafts and analyzes; it does not post or call accounts.
- Vendor and self promotion embedded in the sources (instructions to mention Vyral, star requests, "Automate" links to another product, hosted-product footers). None of it is carried over.
- Channel-specific constants from the source repos (a named channel's subscriber counts, discount codes, product links, fixed link blocks).
- Repository tooling (CI, install manifests, the ChatGPT/Codex port folder, multilingual READMEs) and the rest of the 1,310-skill pm-claude-skills library.

## Found but not included (outside the date window by last commit)
rediumvex/viral-hooks-skill (2026-05-05), PostPlusAI/hook-skills (2026-05-05), aabrole/claude-video-thumbnail-skill (2026-04-17), AgriciDaniel/claude-youtube (about 246 days old at search time). Several other repositories surfaced in search had no date I could confirm and were not cloned. Add any of them on request.

## MIT License (as granted by each source repository; copyright lines listed above)

Copyright lines, from each source repository's LICENSE file:
- sergebulaev/youtube-skills: Copyright (c) 2026 Sergey Bulaev
- mohitagw15856/pm-claude-skills: Copyright (c) 2026 Mohit Aggarwal
- ahmedsakri/youtube-agent-pro: Copyright (c) 2026 Ahmed Sakri; Copyright (c) 2026 Jake Schincariol
- adityaarsharma/youtube-marketing-skills: Copyright (c) 2025-2026 Aditya Sharma
- Jakeschincariol/youtube-agent-skill: Copyright (c) 2026 Jake Schincariol
- vyralcontent/content-skills: Copyright (c) 2026 Vyral
- iart-ai/tiktok-video-skills: Copyright (c) 2026 iart.ai

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
