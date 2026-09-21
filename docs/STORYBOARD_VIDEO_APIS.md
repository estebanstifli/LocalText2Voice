# Direct video providers in Video Storyboard

Implemented 2026-09-21. No paid generation was used during validation. Credentials and model access must be enabled in the provider account before live use.

## Settings and models

Choose a provider in Settings > Video Storyboard > Video generation provider. Each direct provider has its own API key, API URL, resolution, audio control and timeout. New providers default to 720P. Existing provider selections and credentials are preserved. No JSON editor is exposed.

| Provider | Models | Output and durations | Audio |
| --- | --- | --- | --- |
| Alibaba Cloud | Wan 2.6 I2V Flash, **Wan 3.0 standard**, Wan 3.0 Prime, Wan 2.7 I2V | 720P/1080P. Flash/2.7: 2–15s; 3.0: 2–30s | Optional except 2.7; silent discount only for Flash |
| LTX | **LTX 2.3 Fast**, **LTX 2.5 Fast** | 720P/1080P, 6–20s in two-second steps, 24 fps | Optional |
| MiniMax | **MiniMax H3** | Native 768P, fitted to 1280×720; 4–15s | Always generated |
| BytePlus LAS | **Seedance 2.5** (`dreamina-seedance-2-5-260628`) | 720P, 4–30s | Optional |
| LiteLLM / Google | Existing Veo models, including Veo 3.1 Lite | Existing integration retained | Existing behavior retained |

Video durations are rounded up to the next supported duration, capped at the model maximum. The downloaded video is fitted to the exact storyboard scene duration. Native audio is preserved, retimed with the clip and uses the existing scene audio controls. New direct adapters normalize video frames to the selected 16:9 dimensions during that same FFmpeg pass.

### Reference modes

- Alibaba: unchanged, one starting image. None plus one image also uses it as the first frame.
- LTX: Start uses the scene frame; None plus one image animates that image; None without references selects text-to-video. End-only and multiple references are rejected before submitting a job.
- MiniMax: Start/end use a single scene frame in the corresponding role; None accepts up to 9 reference images or text only.
- Seedance: Start uses one scene frame; None accepts up to 30 reference images or text only. End-only is not offered by this integration.

Reference images are encoded locally as data URIs; no separate image-hosting setup is required. LTX's 7 MB encoded-image limit is checked; oversized PNGs get a deterministic JPEG representation when possible. Other provider size/count/dimension limits are validated before submission. First-and-last frame pairs, reference audio/video and asset-library IDs are not exposed by this integration.

### Indicative pricing

USD per generated second, excluding tax/credits. Costs follow generated duration, which can exceed scene duration. Verify current account prices before large batches.

- Wan 2.6 Flash at 720P: $0.025 silent / $0.05 with audio.
- Wan 3.0 standard at 720P: $0.10 list; the advertised 30% promotion gives $0.07. The UI uses the list rate. Prime is a different model, $0.14/s at 720P.
- LTX 2.3 Fast: $0.03 at 720P / $0.06 at 1080P.
- LTX 2.5 Fast: $0.09 at 720P / $0.13 at 1080P.
- MiniMax H3: $0.08 at native 768P.
- Seedance 2.5 via **BytePlus LAS Johor**, without input video: $0.303 × 1.525 = **$0.462075/s** at 720P. This is not the earlier ~$0.23/s estimate for another route. Requires a LAS API key; it is not an interchangeable ModelArk endpoint.
- Veo 3.1 Lite: Google lists $0.05/s at 720P; retained through LiteLLM.

## Internal architecture

- `direct_video_models.py`: models, duration/resolution constraints, audio/reference capabilities, defaults, normalization and indicative pricing for the new providers. The existing Wan catalog stays in `video_storyboard_video_dashscope.py` for backward compatibility.
- `video_provider_protocol.py`: `VideoRequest`, `VideoStatus`, and the small adapter protocol.
- `video_provider_ltx.py`, `video_provider_minimax.py`, `video_provider_byteplus.py`: provider payload builders and response parsers. They do not implement their own polling/download loop.
- `video_provider_jobs.py`: common submission, polling, cancellation, task recovery, result download and final media fitting. Alibaba now uses this runner too.
- `video_storyboard_video_direct.py`: adapter registry and router entry point.
- `storyboard_direct_video_settings.py`: a shared form driven by the capability catalog. Settings and profile snapshots retain each provider independently.

Adding a model on an existing API normally requires a catalog entry and payload-contract tests. A new provider needs a catalog/default entry and an adapter that builds its request and parses submit/status responses, then registration. The shared runner handles the lifecycle. Reference help and translations should match the new adapter's supported modes. There is no arbitrary request editor or user-maintained request schema.

### Recovery and credentials

Jobs are stored in `<provider>_jobs` beside generated candidates. Records contain task IDs and lifecycle flags, never API keys, prompts or image payloads. Existing Wan recovery fingerprints and `dashscope_jobs` records remain compatible.

- A confirmed task resumes after a polling/download error, local cancellation, timeout or local processing failure.
- Successful consumed tasks and explicitly failed tasks allow a new generation.
- A submission with no confirmed task ID after an ambiguous response is blocked from automatic resubmission. Check the provider console and the indicated recovery record first.
- Expired/missing confirmed tasks are reported without automatically submitting another paid task.
- In-process concurrent identical requests are rejected; provider credentials are sent only to API requests, never signed result downloads. Provider errors redact configured secrets.
- Cancellation stops local polling, not the provider's remote paid job. Retry resumes the known remote task where still retained by the provider.

## Validation

HTTP-mocked tests cover provider payloads, status parsing, audio/resolution/duration behavior, download authentication boundaries, terminal failures, ambiguous submissions, cancellation, recovery, missing tasks, settings/profile persistence, dialog reference modes and batch wiring. Real FFmpeg checks cover 768P → 720P and duration fitting, both with audio and with silent input. Live paid provider generation remains untested.

## Official API references

- [Alibaba Wan 3 API](https://www.alibabacloud.com/help/en/model-studio/wan3-video-generation-api-reference), [pricing](https://www.alibabacloud.com/help/en/model-studio/model-pricing).
- [LTX async jobs](https://docs.ltx.io/async-jobs), [LTX 2.3](https://docs.ltx.io/models/ltx-2-3), [LTX 2.5](https://docs.ltx.io/models/ltx-2-5), [input formats](https://docs.ltx.io/input-formats), [pricing](https://docs.ltx.io/pricing).
- [MiniMax H3 creation](https://platform.minimax.io/docs/api-reference/video-generation-v2-create), [task query](https://platform.minimax.io/docs/api-reference/video-generation-v2-query), [pricing](https://platform.minimax.io/docs/guides/pricing-paygo).
- [BytePlus LAS Seedance generation and pricing](https://docs.byteplus.com/en/docs/Byteplus_LAS/video_gen_enhanced).
- [Google Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing).
