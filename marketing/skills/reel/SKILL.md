> Built from the reels-script skill(s). The owner may replace this file.

# Reel maker (OpenFilm)

Build the deliverable now. Dates in the inputs (start dates, due dates, "do not start before") are planning dates for the tracker. They never block this step. Never return an empty result and never ask the owner a question. If something is unclear, make the best choice from the brief and explain it in notes.

Make one vertical video, 1080 × 1920, as one OpenFilm project. The presenter (the character in the character block) speaks the approved script on screen; pages carry the visuals around the presenter, so the presenter is the spine but not the whole frame.

Length: 30 seconds.

Structure:
1. The presenter's clips, in the order of the approved script, in the first section. p1 is on screen in the first 2 seconds.
2. Full-frame scene pages, in the second section, back to back from 0 to the end of the last presenter clip. Each one changes every 2 to 4 seconds, and its text sits in the top 900 px, because the presenter covers the bottom half.
3. The last presenter line is the call to action "Link in bio".

How to build it:
- The presenter clips go in the first `<section>` in `<body>`, one `<video class="presenter">` per clip, back to back. Each clip's `at` is the value in presenter.json for it, exactly. OpenFilm rejects a clip outside a section ("outside any track"). Clips in one section must not overlap in time. The first section is on top, so the presenter covers the pages in the second section.
- Each presenter clip is `<video class="presenter" src="assets/p1.mp4" at="0" style="left: 0px; top: 960px; width: 1080px; height: 960px">`. The box is the bottom half of the frame. The `object-fit: cover; object-position: top` rule for `.presenter` goes in the film's `<style>` block, not in the clip's style.
- A clip's style places it only with left, top, width, height and rotate. Never put position, display, inset, right, bottom, margin, transform, translate, scale, z-index, visibility, animation or transition in a clip's style.
- Set the viewport meta tag to the stage size. Without it OpenFilm uses 1920 × 1080, which is wrong for this reel. Use this shape:

```html
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=1080, height=1920">
<style>
  .presenter { object-fit: cover; object-position: top; }
</style>
</head>
<body>
<section>
  <video class="presenter" src="assets/p1.mp4" at="0" style="left: 0px; top: 960px; width: 1080px; height: 960px"></video>
  <video class="presenter" src="assets/p2.mp4" at="5" style="left: 0px; top: 960px; width: 1080px; height: 960px"></video>
  <video class="presenter" src="assets/p3.mp4" at="10" style="left: 0px; top: 960px; width: 1080px; height: 960px"></video>
</section>
<section>
  <iframe src="scenes/hook.html#t=0,3.5" at="0" style="left: 0px; top: 0px; width: 1080px; height: 1920px"></iframe>
  <iframe src="scenes/proof.html#t=0,3.5" at="3.5" style="left: 0px; top: 0px; width: 1080px; height: 1920px"></iframe>
  <iframe src="scenes/offer.html#t=0,3.5" at="7" style="left: 0px; top: 0px; width: 1080px; height: 1920px"></iframe>
  <iframe src="scenes/link.html#t=0,3.5" at="10.5" style="left: 0px; top: 0px; width: 1080px; height: 1920px"></iframe>
</section>
</body>
</html>
```

- Nothing else goes in `<body>` except `<section>` tracks.
- The second `<section>` lists the pages in order. Each `<iframe>` has `src="scenes/offer.html#t=0,3.5"`, where `offer.html` is the scene file and `3.5` is its length in seconds, and `at="7"`, where `7` is the second it starts. The first page has `at="0"`. Each page starts when the one before it ends, and the last page ends where the last presenter clip ends. The `at` values and lengths in the example show the shape only: take the presenter's `at` values from presenter.json.
- Each scene sets `window.film = { frame(t) {...}, duration, width: 1080, height: 1920 }`.
- Every scene must move: text slides or fades in. A video that never changes is not allowed.
- On-screen text must be large and readable without sound. Keep it at least 120 px from the top edge and 60 px from the sides, and inside the top 900 px.
- The presenter's voice is the only audio. Add no other audio.
- The presenter must be on screen in the first 2 seconds, so the p1 clip starts at 0.
- Inline CSS only. Do not load anything from the internet.
- Use only facts from the brief's Facts section.
- Change what is on screen every 2 to 4 seconds.
- Follow the constraints the brief gives.
- No em dashes.

Follow the design system block exactly: use only its colours, its font stack and sizes, its layout and its logo. Use no colour that is not in its Colors section. Follow its Do not list.
