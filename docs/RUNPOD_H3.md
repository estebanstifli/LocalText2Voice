# H3 Workflow Prototype (In Development)

> **In development. Not ready for public use.** This private integration is a
> development test, not a finished feature or a publicly available service.

The goal is to support H3 JSON workflows locally through ComfyUI or remotely
through Runpod, hosted ComfyUI or other compatible services. That general-purpose
integration is not implemented yet. The notes below describe the current private
test setup only; a normal ComfyUI installation or Runpod endpoint is not enough
to use this prototype. Keep it out of the README until public support is ready.

The Runpod provider supports two different contracts. Public models continue to
use their existing Wan/Kling adapters. **MiniMax H3 · In development**
is for the custom queue worker running the private test H3 workflows; it is not
MiniMax's commercial API or a public Runpod Hailuo endpoint.

## Settings → Video Storyboard → Video generation → Runpod

1. For development testing only, select **MiniMax H3 · In development**.
2. Under **Advanced**, enter the private endpoint ID (or its Runpod URL).
3. Reuse your Runpod API key. It remains in encrypted application settings,
   never in the storyboard or workflow JSON.
4. Choose **Fast · Turbo 4 steps** or **Normal · 20 steps**.
5. Optionally enter the current GPU hourly rate for execution-only estimates.
   Zero means unknown, not free.
6. The endpoint must be enabled in Runpod (Max workers at least 1). The app
   does not create, activate, resize, or pause infrastructure automatically.

The supported private handler accepts one **starting** frame, 1–10 seconds,
`preset`, `prompt`, `seed`, `seconds`, `images` (base64) and `return_base64:true`.
The validated workflow fixes output near 1344×768 at 24 FPS. The dropdown does
not override remote workflow dimensions. It rounds frames to its 17*n+5 rule;
existing local post-processing fits the resulting clip to the scene duration.
The remote workflow includes audio. Disable clip audio in the storyboard to
keep the separate voice-over; that setting does not eliminate remote audio
generation cost.

References go directly to your Runpod worker, without Cloudflare/S3 image
hosting. The app caps input images at 7 MB to leave room for base64 expansion.
Output must contain one MP4 in `output.files[].base64`. The existing worker
limits inline output to 7 MB. If it returns only a volume path, the app preserves
the paid job and reports recovery instructions instead of silently resubmitting.
Do not press “Allow new generation” simply to recover a missing download.

## Timing and batch tests

Use **Generate storyboard videos**, select e.g. scenes `1–10`, and leave the
additional motion instruction blank when each scene already has its own video
prompt. Jobs are sequential and completed clips are accepted individually.

**Runpod jobs → Export timing report (CSV)** includes the server queue/compute
times, worker ID, local save/post-processing time, elapsed time to the ready clip,
and the execution-only price estimate. Credentials, prompts and image data are
not exported. Inspect worker IDs and queue times to identify cold versus warm
requests. Queue time is not the same as GPU startup billing. A real cost report
must include startup, idle and storage, and reconcile with Runpod billing.

The GUI's “Stop after current video” stops the local batch, not infrastructure.
Keep Min workers 0; set Max workers 0 in Runpod when you want an explicit pause.
For benchmarks, allow enough idle time for downloading and local processing
between requests so every clip does not trigger another cold start.

Restart the desktop application and its idle EngineHost after updating source.
Do not restart while a generation job is active.
