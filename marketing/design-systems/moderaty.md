# Design system: moderaty

Source: Advanced-Digital-Marketing-LTDA/Moderaty, main at commit c9a0ea2: src/app.css (colours, type, radius, spacing) and static/favicon.svg (logo).

## Colors
Colours the images and reel may use. Only these colours may appear.
- background: #0a0a0c
- text: #f4f4f2
- accent: #ff3131
- surface: #101014
- surface-2: #16161b
- text-2: #9b9ba4
- text-3: #5c5c66
- ok: #3ddc84
- amber: #f5b84c
- line: #ffffff at 9% opacity
- line-strong: #ffffff at 16% opacity

Hairline borders are 1px wide. Text-3 is for non-essential labels only: it is 2.99:1 on the background, below the 4.5:1 target.

## Typography
Font stack (system fonts only, nothing loaded from the internet), headline weight, body weight, and sizes for stills and for the reel.
- font stack: 'Archivo', system-ui, -apple-system, 'Segoe UI', sans-serif
- labels and numbers: 'JetBrains Mono', ui-monospace, monospace (caps labels only)
- headline weight: 700
- body weight: 400 (the source does not set a body weight)
- caps labels: 12 px, uppercase, weight 700, letter-spacing 0.08em (the source's button label style)
- stills: headline 72 to 96 px, second line 36 to 44 px
- reel: headline 96 to 120 px, second line 36 to 44 px

Archivo and JetBrains Mono are not system fonts. The images and reel must not load them, so the stack falls back to the system fonts after them.

## Layout
Margins, alignment, where the headline sits, where the logo sits.
- margins: 64 px from every edge on stills; 120 px from the top and bottom on the reel
- alignment: headline left-aligned, vertically centred, accent bar under it
- logo: the source does not set a placement; the owner decides. Until then, leave the logo out.
- spacing scale (use these steps only): 4, 8, 16, 24, 32, 48, 64, 96 px

## Logo
Inline SVG markup to paste into scenes, or "None."
Copy this exactly. Do not redraw it.

```html
<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64"><title>Moderaty</title><rect width="64" height="64" fill="#0a0a0c"/><polygon points="12,46 12,14 21,14 32,33 43,14 52,14 52,46 44,46 44,29 35,45 29,45 20,29 20,46" fill="#f4f4f2"/><rect x="12" y="51" width="40" height="4" fill="#ff3131"/></svg>
```

## Imagery
Shapes, lines and motifs that may be used.
- square corners only: a 1px hairline border or one accent bar under the headline
- no rounded shapes, no shadows, no gradients

## Do not
Things the images and reel must never do.
- Do not add radius, shadows or gradients.
- Do not use the accent colour for anything except the one accent bar under the headline.
- Do not use text-3 for essential text.
- Do not redraw the logo or change its colours.
- Do not put jokes in coverage limits, irreversible actions, pricing or privacy statements.
- Do not claim accuracy figures or "catches everything".
- Do not load fonts or images from the internet.
