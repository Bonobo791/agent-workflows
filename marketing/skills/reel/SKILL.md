> Built from the reels-script skill(s). The owner may replace this file.

# Reel maker (OpenFilm)

Build the deliverable now. Dates in the inputs (start dates, due dates, "do not start before") are planning dates for the tracker. They never block this step. Never return an empty result and never ask the owner a question. If something is unclear, make the best choice from the brief and explain it in notes.

Make one vertical video, 1080 × 1920, as one OpenFilm project. The presenter (the character in the character block) speaks the approved script on screen; pages carry the visuals around the presenter, so the presenter is the spine but not the whole frame.

Length: 30 seconds.

Structure:
1. The presenter video, the whole approved script in one take, in the first section, starting at 0. The presenter is on screen in the first 2 seconds.
2. Full-frame scene pages, in the second section, back to back from 0 to the end of the presenter video. Each one changes every 2 to 4 seconds, and its text sits in the top 900 px, because the presenter covers the bottom half.
3. The last card carries the call to action from the brief.

How to build it:
- The presenter video goes in the first `<section>` in `<body>`, as one `<video class="presenter">` that starts at `at="0"`. Its `at` is the value in presenter.json, exactly. OpenFilm rejects a video outside a section ("outside any track"). The first section is on top, so the presenter covers the pages in the second section.
- The presenter video is `<video class="presenter" src="assets/presenter.mp4" at="0" style="left: 0px; top: 960px; width: 1080px; height: 960px">`. The box is the bottom half of the frame. The `object-fit: cover; object-position: top` rule for `.presenter` goes in the film's `<style>` block, not in the video's style.
- The presenter video's style places it only with left, top, width, height and rotate. Never put position, display, inset, right, bottom, margin, transform, translate, scale, z-index, visibility, animation or transition in its style.
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
  <video class="presenter" src="assets/presenter.mp4" at="0" style="left: 0px; top: 960px; width: 1080px; height: 960px"></video>
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
- The second `<section>` lists the pages in order. Each `<iframe>` has `src="scenes/offer.html#t=0,3.5"`, where `offer.html` is the scene file and `3.5` is its length in seconds, and `at="7"`, where `7` is the second it starts. The first page has `at="0"`. Each page starts when the one before it ends, and the last page ends where the presenter video ends. The `at` values and lengths in the example show the shape only: take the presenter video's length from presenter.json.
- Each scene sets `window.film = { frame(t) {...}, duration, width: 1080, height: 1920 }`.
- Every scene must move: text slides or fades in. A video that never changes is not allowed.
- On-screen text must be large and readable without sound. Keep it at least 120 px from the top edge and 60 px from the sides, and inside the top 900 px.
- The presenter's voice is the only audio. Add no other audio.
- The presenter must be on screen in the first 2 seconds, so the presenter video starts at 0.
- Inline CSS only. Do not load anything from the internet.
- Use only facts from the brief's Facts section.
- Change what is on screen every 2 to 4 seconds.
- Follow the constraints the brief gives.
- No em dashes.

Follow the design system block exactly: use only its colours, its font stack and sizes, its layout and its logo. Use no colour that is not in its Colors section. Follow its Do not list.
