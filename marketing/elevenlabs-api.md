# ElevenLabs API: what the reel step uses

Read on 2026-11-07 from ElevenLabs' developer reference. Pages read:

- Create speech with timestamps: https://elevenlabs.io/docs/api-reference/text-to-speech/convert-with-timestamps
- Text to speech capabilities: https://elevenlabs.io/docs/overview/capabilities/text-to-speech
- Models: https://elevenlabs.io/docs/overview/models
- Best practices: https://elevenlabs.io/docs/overview/capabilities/text-to-speech/best-practices
- List voices: https://elevenlabs.io/docs/api-reference/voices/search
- Avatars: https://elevenlabs.io/docs/overview/capabilities/image-video/avatars

## Auth and base URL

- Base URL: `https://api.elevenlabs.io`
- Header: `xi-api-key: <key>`. The code reads the key from the `ELEVENLABS_API_KEY` environment variable.

## Create speech with timestamps: POST /v1/text-to-speech/{voice_id}/with-timestamps

- Body (JSON): `text` and `model_id` (we send `eleven_v4`). Every other field keeps its default.
- Response (200): `audio_base64` (the MP3) and `alignment`, with three parallel lists: `characters`, `character_start_times_seconds` and `character_end_times_seconds`. The response also has `normalized_alignment`, which we do not use.
- Output: the default, `mp3_44100_128`.
- Errors: HTTP 422, with a `detail` list.
- Limit: 10,000 characters per request for `eleven_v4`.
- Not stated in the docs: whether `eleven_v4` returns `alignment`, and how an audio tag such as `[laughs]` appears in it. The first live run settles both.

## Voices: GET /v2/voices

- Query: `search`, `page_size` (up to 100) and `next_page_token`.
- Response: `voices` (each with `voice_id`, `name` and `category`), `has_more` and `next_page_token`.

## Model and audio tags

- `eleven_v4` is the most expressive model. Audio tags are plain text in square brackets, such as `[laughs]`, `[sighs]` and `[whispers]`.
- The "Enhance" button that proposes tags is in the web app only. No API endpoint for it was found.

## Avatars

- Avatars (a talking-head video from a voice and a photo) have no API. The docs say "API access: not available at launch; planned for future release". The owner makes the avatar clip by hand.

## Price

- `eleven_v4` text to speech: $0.08 per 1,000 characters (pricing page, read 2026-11-07). A 30-second reel costs a few cents.

## Not used

- Pronunciation dictionaries, voice settings, seeds, forced alignment, and the speech-to-text and dubbing APIs.
