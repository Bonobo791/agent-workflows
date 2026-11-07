> Written for this pipeline from ElevenLabs' text-to-speech best practices, read 2026-11-07. The owner may replace this file.

# Voiceover script

The script is the presenter's words, read aloud by ElevenLabs Eleven v4 in the speaking voice. The voice follows the punctuation and the audio tags, so write for the ear.

Rules:
- Punctuation sets the pace: full stops and commas for rhythm, an ellipsis (...) for a hesitation. No SSML tags and no break tags.
- Write numbers, currency, dates, units and abbreviations the way they are said: "forty-two dollars", not "$42"; "Doctor", not "Dr.".
- Use capitals for emphasis sparingly: one word at most in the whole script.
- Audio tags are the only brackets in the script. Use at most 4, and only these: [laughs], [sighs], [gasps], [whispers], [sarcastic], [excited], [playful], [smug]. Put a tag before the words it shapes, at most one per clause, for example: "[sarcastic] Oh, great. Another comment from a bot."
- Never put a tag inside a word, and never write any other bracketed text.
- Tags give delivery only. Never describe a sound or an action in words.
- Write the brand name as Moderaty, in the spoken script too.
- A later pass keeps the audio tags it is given and adds none beyond the limit.
