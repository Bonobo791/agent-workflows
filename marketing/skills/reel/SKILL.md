> Draft skill. The owner will replace this file.

# Reel maker (OpenFilm)

Make one vertical video, 1080 × 1920, between 15 and 30 seconds long, as one OpenFilm project.

Owner: confirm the size and length for your account.

Structure:
1. Hook scene (about 3 seconds): the Instagram image text.
2. Two to four scenes, each showing one supporting point from the brief.
3. Final scene: the call to action "Link in bio".

How to build it:
- `film.html` lists the scenes in order. Each `<iframe>` has `src="scenes/<file>.html#t=0,<length>"` and `at="<start second>"`. Each scene starts when the one before it ends. Every iframe is `left: 0px; top: 0px; width: 1080px; height: 1920px`.
- Each scene sets `window.film = { frame(t) {...}, duration, width: 1080, height: 1920 }`.
- Every scene must move: text slides or fades in. A video that never changes is not allowed.
- On-screen text must be large and readable without sound. Keep it at least 120 px from the top and bottom edges and 60 px from the sides.
- No audio.
- Use system fonts and inline CSS only. Do not load anything from the internet.
- Use only facts from the brief's Facts section.
