> Draft skill. The owner will replace this file.

# Image maker (OpenFilm)

Make three still images, one OpenFilm project each:

| name | size (width × height) | uses |
| --- | --- | --- |
| linkedin | 1200 × 627 | the LinkedIn post's main idea |
| x | 1600 × 900 | the X post's main idea |
| instagram | 1080 × 1080 | the Instagram post's "Image text" |

Owner: confirm these sizes for your accounts.

How an OpenFilm project works:
- `film.html` at the project root places scenes with `<iframe>` elements. `src` is the scene file plus `#t=start,end` in seconds. `at` is when the scene starts. The iframe's style sets its position and size.
- Each scene is an HTML file with a script that sets `window.film = { frame(t) { ... }, duration, width, height }`. `frame(t)` is called with the time in seconds and must put the page into its state at that time.
- The still is captured at `look_time`. Everything must be fully visible at that time. Use a `look_time` near the end of the scene.

Working example (1080 × 1080, 3 seconds):

film.html:
```html
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=1080, height=1080">
</head>
<body>
<section>
  <iframe id="card" src="scenes/card.html#t=0,3" at="0" style="left: 0px; top: 0px; width: 1080px; height: 1080px"></iframe>
</section>
</body>
</html>
```

scenes/card.html:
```html
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>
  html, body { margin: 0; width: 1080px; height: 1080px; background: #111; overflow: hidden; }
  #title { position: absolute; left: 80px; top: 440px; margin: 0; font: 700 96px sans-serif; color: #fff; }
</style>
</head>
<body>
<h1 id="title">Example Domain</h1>
<script>
  window.film = {
    frame(t) {
      document.getElementById('title').style.opacity = Math.min(1, t / 0.5);
    },
    duration: 3,
    width: 1080,
    height: 1080,
  };
</script>
</body>
</html>
```

Design rules:
- Text on the image: a headline of eight words or fewer and, optionally, one short line under it.
- Large text, strong contrast, at least 60 px from every edge. No text may overlap other text or be cut off.
- Use system fonts and inline CSS only. Do not load anything from the internet.
- The three images share one look: the same colors and fonts.
- Use only facts from the brief's Facts section.
