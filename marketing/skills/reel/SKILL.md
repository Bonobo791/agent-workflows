> Built from the reels-script skill(s). The owner may replace this file.

# Reel maker (OpenFilm)

Build the deliverable now. Dates in the inputs (start dates, due dates, "do not start before") are planning dates for the tracker. They never block this step. Never return an empty result and never ask the owner a question. If something is unclear, make the best choice from the brief and explain it in notes.

Make one vertical video, 1080 × 1920, between 15 and 30 seconds long, as one OpenFilm project.

Structure:
1. Hook scene (about 3 seconds): the Instagram image text.
2. Two to four scenes, each showing one supporting point from the brief.
3. Final scene: the call to action "Link in bio".

How to build it:
- Put every `<iframe>` clip inside one `<section>` in `<body>`. OpenFilm rejects a clip outside a section ("outside any track"). Clips in one section must not overlap in time; a title over footage goes in a second `<section>`.
- Set the viewport meta tag to the stage size. Without it OpenFilm uses 1920 × 1080, which is wrong for this reel. Use this shape:

```html
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=1080, height=1920">
</head>
<body>
<section>
  <iframe src="scenes/hook.html#t=0,3" at="0" style="left: 0px; top: 0px; width: 1080px; height: 1920px"></iframe>
  <iframe src="scenes/export.html#t=0,6" at="3" style="left: 0px; top: 0px; width: 1080px; height: 1920px"></iframe>
</section>
</body>
</html>
```

- Nothing else goes in `<body>` except `<section>` tracks.
- `film.html` lists the scenes in order. Each `<iframe>` has `src="scenes/export.html#t=0,6"`, where `export.html` is the scene file and `6` is its length in seconds, and `at="3"`, where `3` is the second it starts. The first scene has `at="0"`. Each scene starts when the one before it ends. Every iframe is `left: 0px; top: 0px; width: 1080px; height: 1920px`.
- Each scene sets `window.film = { frame(t) {...}, duration, width: 1080, height: 1920 }`.
- Every scene must move: text slides or fades in. A video that never changes is not allowed.
- On-screen text must be large and readable without sound. Keep it at least 120 px from the top and bottom edges and 60 px from the sides.
- No audio.
- Inline CSS only. Do not load anything from the internet.
- Use only facts from the brief's Facts section.
- Change what is on screen every 2 to 4 seconds.
- Follow the constraints the brief gives.
- No em dashes.

Follow the design system block exactly: use only its colours, its font stack and sizes, its layout and its logo. Use no colour that is not in its Colors section. Follow its Do not list.
