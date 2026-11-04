# HeyGen API: what the reel step uses

Read on 2026-11-04 from the HeyGen developer reference at developers.heygen.com. Pages read:

- Create video: https://developers.heygen.com/reference/create-video.md
- Get video (status and downloads): https://developers.heygen.com/reference/get-video.md
- Video details (subtitle and caption fields): https://developers.heygen.com/video-details.md
- Captions section: https://developers.heygen.com/studio-videos.md
- List avatar looks: https://developers.heygen.com/reference/list-avatar-looks.md
- List voices: https://developers.heygen.com/reference/list-voices.md
- Generate speech (word timestamps, audio only): https://developers.heygen.com/reference/generate-speech.md
- Documentation index: https://heygen-1fa696a7.mintlify.site/llms.txt

## Auth and base URL

- Base URL: `https://api.heygen.com`
- Header: `x-api-key: <key>`, a key from the HeyGen dashboard. The code reads it from the `HEYGEN_API_KEY` environment variable.
- Optional header `Idempotency-Key` (we do not send it).

## Create a video: POST /v3/videos

Content-Type `application/json`. Fields we send:

- `type`: `"avatar"` (required)
- `avatar_id`: a look id from `GET /v3/avatars/looks`, the `data[].id` field (required)
- `script`: the spoken text, at least one character
- `voice_id`: from `GET /v3/voices`, the `voice_id` field. Left out, the avatar's default voice is used.
- `aspect_ratio`: `"9:16"`. There are no width and height fields; the pixel size follows `resolution` and `aspect_ratio`.
- `resolution`: `"1080p"`. The default is `"720p"`; `"4k"` is limited to some avatar types.
- `output_format`: `"mp4"`

Response (HTTP 200): `data.video_id` (string) and `data.status`, which starts as `"waiting"`.

We send `caption` as `{"file_format": "srt"}`, so that `subtitle_url` is returned. We do not set its `style`, so no captions are burned in. We send no other optional field.

## Status: GET /v3/videos/{video_id}

- `data.status`: `pending`, `processing`, `completed` or `failed`. Anything else counts as not finished.
- When `completed`: `data.video_url` (a presigned MP4 link) and `data.duration` (a number, in seconds).
- Subtitle files: `data.subtitle_url` (an SRT sidecar, present only when captions were requested) and `data.captioned_video_url` (an MP4 with captions burned in).
- On failure: the quick start names `failure_code` and `failure_message` on the same response. Check them against a real failed response before relying on them.

## Avatars: GET /v3/avatars/looks

- Query (all optional): `avatar_type` (`studio_avatar`, `digital_twin` or `photo_avatar`), `ownership` (`public` or `private`), `group_id`, `limit` (1 to 50, default 20), `token`.
- Items are in `data[]`. Each item has `id` (the look id, which is the `avatar_id` we send) and `name`.
- Paging: `has_more` (boolean) and `next_token`. Send `next_token` back as the `token` query parameter while `has_more` is true.

## Voices: GET /v3/voices

- Query (all optional): `type` (`public` by default, or `private`), `engine`, `language`, `gender`, `limit` (1 to 100, default 20), `token`.
- Items are in `data[]`. Each item has `voice_id` (the id we send) and `name`.
- Paging: `has_more` and `next_token`, as for avatars.

## Duration and size

- Duration: `data.duration` on the status response, in seconds.
- Size: 9:16 is set with `aspect_ratio`. The docs give no pixel dimensions.

## Captions and word timings

- Captions from a video are an SRT file (`file_format` is `"srt"`, the only value), returned as `subtitle_url`, plus a burned-in MP4 as `captioned_video_url`.
- The docs do not say whether those captions have word-level timings, and they do not say how fine the timing is.
- Word timings appear only in `POST` generate speech, as `data.word_timestamps` (a `word`, `start` and `end` in seconds for each word). That is a separate audio call, not part of an avatar video.

Result: word timings are not available for avatar video. The SRT file from `subtitle_url` is still converted to WebVTT, with one cue for each SRT cue and no word timings.
