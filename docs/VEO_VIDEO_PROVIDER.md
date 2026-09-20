# Veo scene videos through LiteLLM

In **Settings → Video Storyboard → Video provider**, choose **LiteLLM · Veo**.
The current Gemini catalog offers Veo 3.1, Veo 3.1 Fast and Veo 3.1 Lite
(preview models). Fast is the default; older Veo 3.0 models are deprecated.

Enter a Gemini API key and leave URL empty to use the LiteLLM Python SDK.
Alternatively, enter a LiteLLM proxy URL and its virtual key. The proxy must
enable Google AI Studio pass-through (`/gemini`) and have Gemini credentials.
Generation, polling and download then use that proxy. This is not the proxy's
OpenAI-compatible `/videos` route: pass-through preserves Veo reference fields.

In **Generate scene video**:

- **Frame at start** uses the scene image as the first frame.
- **None**, without references, generates from the prompt alone.
- **None + Img Ref** sends character, location, object or file references to
  Veo 3.1 / Fast. Google documents up to three asset images; the app sends all
  selected references and displays any provider limit error. Lite does not
  support asset references.
- End-frame-only generation is unavailable: Veo requires a starting image with
  a last frame. The app does not reverse clips with speech to simulate this.

720p clips use 4, 6 or 8 seconds, rounding up to the scene duration up to 8.
Reference generation and 1080p use 8 seconds. The result is fitted to the scene's
duration, retaining its audio; existing video audio gain and mute controls apply.
Long scenes therefore slow down the generated clip; they are not video extension.

Pending operation IDs are saved under the project's video candidate directory
in `litellm_jobs`. Retrying after a polling/download interruption resumes the same
operation. A consumed result or a newly accepted clip starts a new generation.
An ambiguous submission without an operation ID is not automatically submitted
again, to avoid duplicate charges. Provider errors appear in the generation
dialog with credentials redacted.

Implementation requires LiteLLM 1.99 or newer. A request-scoped HTTP adapter
supplies `referenceImages` inside `instances[0]` and retains raw operation errors,
which version 1.99's normal video translation does not fully support. The SDK
and its global configuration are not patched.

Sources verified September 2026:

- [Google Veo documentation and model versions](https://ai.google.dev/gemini-api/docs/veo)
- [LiteLLM Veo SDK](https://docs.litellm.ai/docs/providers/gemini/videos)
- [LiteLLM Google AI Studio pass-through](https://docs.litellm.ai/docs/pass_through/google_ai_studio)
