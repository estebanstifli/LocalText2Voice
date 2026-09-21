# Alibaba Cloud Wan video providers

In Settings > Video Storyboard > Video provider, choose **Alibaba Cloud · Wan**, then select the model. Enter the Model Studio API key for the configured Singapore workspace. Its DashScope `/api/v1` URL is prefilled; an OpenAI-compatible `/compatible-mode/v1` URL is also normalized to DashScope. The API key remains in local settings, following the existing provider settings convention.

- Models: `wan2.6-i2v-flash`, `wan3.0-video`, `wan3.0-video-prime`, `wan2.7-i2v`. Existing settings default to Flash. Switching model in the UI selects 720P.
- Singapore 720P list prices, checked 2026-09-20: Flash silent $0.025/s, Wan 3.0 standard $0.10/s list, Prime $0.14/s, Wan 2.7 $0.10/s, excluding tax and credits.
- Resolution: `720P` or `1080P`; aspect ratio follows the input image.
- Duration: scene duration rounded up to a whole second, bounded to 2–15 seconds for Flash/2.7 and 2–30 seconds for Wan 3.0 standard/Prime. The downloaded clip is fitted to the scene duration using the existing video pipeline.
- The Generate audio checkbox sends `parameters.audio` for Flash and both Wan 3.0 variants (off by default). Wan 2.7 always generates audio, so its checkbox is checked and disabled and no unsupported parameter is sent. Generated audio is preserved when fitting clips and uses the existing storyboard scene audio controls. Only Flash gets a silent-generation discount: $0.025/s silent versus $0.05/s with audio at 720P. Disabling audio on Wan 3.0 does not reduce its API price.
- This integration uses the scene frame at the start, or None plus one image reference. End frames and multiple references remain outside the scope of these tests and are rejected before submission.
- Requests use DashScope's asynchronous video-synthesis and task endpoints. Flash sends `input.img_url`; Wan 3.0 standard/Prime and 2.7 send `input.media` with `type=first_frame`. Local images are sent as Base64; no public image hosting is required.
- Task IDs are stored under `dashscope_jobs` beside generated clips. Retrying after a polling failure, local cancellation, or timeout resumes the confirmed task. An uncertain submission without a confirmed task ID is blocked from automatic resubmission to avoid duplicate charges; inspect Model Studio and the indicated recovery record first. Successfully consumed or explicitly failed tasks allow a new generation.
- Downloads use the signed result URL without sending the API key. Errors redact configured credentials.

No live paid generation is part of the automated tests. Tests use mocked HTTP responses for payloads, polling, failures, task recovery and downloads, plus settings/profile and dialog checks.

Official reference: https://www.alibabacloud.com/help/en/model-studio/legacy-image-to-video-api-reference

New model references:
- https://www.alibabacloud.com/help/en/model-studio/wan3-video-generation-api-reference
- https://www.alibabacloud.com/help/en/model-studio/image-to-video-general-api-reference
- https://www.alibabacloud.com/help/en/model-studio/model-pricing

Submission, polling, recovery and downloads use the shared `video_provider_jobs` runner. Existing Wan task records stay compatible. See [all direct providers](STORYBOARD_VIDEO_APIS.md).
