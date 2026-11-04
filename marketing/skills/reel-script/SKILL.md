> Built from the short-form-video-script and reels-script skill(s). The owner may replace this file.

# Reel script writer

Write the presenter's spoken script for one vertical reel: the lines the character says on screen, in order.

Return JSON as described in the output format: "presenter" is the character's name from the character block, "lines" is the script in order (each line has "id", p1, p2, p3 and so on, and "text"), and "notes".

Rules:
- Each line is 25 words or fewer. The whole script is at most 2.5 words per second of the reel length in the reel skill.
- The first line names a moment, a number or a mistake the reader recognises, from the brief's Angle or the Reader problems in positioning.md.
- Use only facts from the brief's Facts section. Never invent a fact. Keep to the Claims we never make in positioning.md.
- Write each line as the character would say it aloud: short sentences, plain words, no stage directions, no emoji.
- One idea per reel. Say what the reader should do once, near the end.
- The last line ends with "Link in bio" (a final period is allowed).
- The disclosure is not part of the script; the reel notes carry it.
- No em dashes.
