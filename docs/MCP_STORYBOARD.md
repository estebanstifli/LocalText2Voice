# Video Storyboard over local MCP stdio

The UI does not need to be running. Start `mcp_stdio_bridge.py` using the project's
Python environment (`.venv/Scripts/python.exe` on Windows). The bridge starts or
reuses EngineHost, which executes and persists visual jobs. MCP HTTP `/mcp` has
been removed; the HTTP API between the bridge, GUI and EngineHost remains.

There are 57 storyboard tools in addition to the 19 audio tools. Their typed
signatures are available through MCP `tools/list`. This guide is also available
as `localtext2voice://docs/storyboard`.

## End-to-end workflow

1. `sb_list_projects(query="title")` finds an existing audiobook. Use its numeric
   ID as a string or its UUID for `project_id`.
2. `sb_get_timed_text` reads source narration and segment timestamps even before
   a storyboard exists. If the initial voice offset is unknown, timestamps are
   labelled `unconfirmed_offset` and `timing_ready=false`. Supply the offset that
   belongs to the selected audio, rather than assuming today's global setting.
3. The connected agent analyzes the text itself. No tool invokes the narrative
   planner. `sb_create_project(project_id=...)` creates an empty storyboard;
   omitting the ID creates a new audiobook/project record.
4. Read `sb_list_styles`, choose a preset ID or a custom style with `sb_set_style`.
5. Create entities, appearance states and scenes with explicit assignments.
   Create scenes in batches with `sb_batch_update_scenes` or `sb_import_prompts`.
6. Generate reference images, frames and clips. These operations return `job_id`;
   poll `sb_get_job`. Inspect frames with `sb_preview` (inline MCP image).
7. Edit prompts, states, references or media as necessary; accept candidates
   with `sb_assign_asset` or `sb_set_reference`.
8. Adjust scene durations, transitions and clip audio. `sb_validate` checks
   render readiness, including contiguous coverage of the narration audio.
9. `sb_render` produces a new MP4 inside the project. Its job result contains
   the absolute output path and asset ID. No window or confirmation dialog is
   needed for this workflow.

## Revisions and persistence

Every project mutation and generation request requires `expected_revision` from
`sb_get_project`. A successful edit returns the new revision. Generation writes
can also advance the revision, so read the project again after a job completes.

Files use the same `storyboard/storyboard.json` format as the GUI. Writes use a
cross-process file lock, atomic replacement and revision checking. A stale GUI
save fails rather than overwriting the agent's changes: reload the storyboard
before continuing edits in that window. Snapshots preserve earlier project data;
restoring one does not delete assets generated later.

Generation is serialized within the EngineHost visual queue. Jobs and per-item
results persist under `storyboard/jobs`. If the project changes while generation
is running, the output is retained as a candidate and is not automatically
assigned. `idempotency_key` prevents duplicate submissions of the same operation
and arguments. Use a fresh key for an intentional new generation.

After a process interruption, a job is reported as `interrupted`. Explicit retry
skips completed scene results. A remote request interrupted before recording its
result can be submitted again and incur another charge; universal provider-side
exactly-once execution is not guaranteed. Cancellation is cooperative; providers
may finish an already accepted request. Completed media remain available.

Closing the GUI keeps the host running while storyboard jobs are active. The
host's `/shutdown` endpoint also rejects shutdown during active storyboard work.

## Text and timing

`sb_get_timed_text` supports `offset`, `limit`, `start_seconds`, `end_seconds`.
It returns cue IDs, text, start/end, timing readiness, audio path/hash, source
revision and the clock offset. These are **segment** timestamps, not word-level
alignment. Changed audio hashes invalidate timing readiness. The source text and
the selected audio must describe the same narration; the agent is responsible
for semantic alignment when importing a different recording.

For SRT/VTT import, pass `format="srt"` or `format="vtt"` and the subtitle `text`.
JSON cue format for `sb_import_timed_text`:

```json
{
  "cues": [
    {"cue_id": "line-1", "start_seconds": 2.0, "end_seconds": 6.2,
     "text": "The witness entered the courtroom.", "timing_ready": true}
  ],
  "voice_start_offset_seconds": 2.0
}
```

All cue times refer to the selected audio, including its initial silence.
`sb_export_timed_text` returns JSON, TXT, SRT or VTT; segment-based subtitle exports
may need editorial splitting for comfortable reading. Pagination is handled
internally when exporting or searching all cues.

## Image styles

`sb_list_styles` returns the real application catalog, with each `id`, `name` and
exact style `prompt`. Apply one as follows (include project ID/revision):

```json
{"style_id": "custom", "custom_prompt": "Restrained graphite drawing on cream paper",
 "palette": "charcoal and warm grey", "lighting": "soft window light",
 "negative": "text, watermark"}
```

Omit `scene_ids` to set the project style. Supply a list of scene IDs for local
overrides. `sb_get_project` exposes the project style and scene overrides.
`sb_compile_scene_prompt` shows the effective prompt and references before any
generation request. Reference generation also uses the project style.

`generation_overrides.prompt_mode="complete"` sends the agent's `raw_prompt`
verbatim (or the scene `prompt` if raw_prompt is empty). Such complete prompts
deliberately bypass style composition. To use a selected style automatically,
use the normal scene `prompt` without complete mode or a raw prompt override.

## Entities, states and explicit scene assignments

Entity types: `character`, `location`, `object`, `group`, `era`. IDs are stable and
entity/state IDs must be unique. Fields include `name`, `aliases`,
`identity_description`, `description`, `states`, `reference_image_path`; groups
can contain `members`. States carry `id`, `description`, `evidence`,
`change_reason`, optional time bounds and `reference_image_path`.

Keep permanent identity separate from temporary clothes, wounds or dirt. Example:

```json
{"entity_type": "character", "entity": {
  "id": "ventura", "name": "Ventura Lustres",
  "identity_description": "Stable facial and physical description",
  "states": [
    {"id": "ventura_clean", "description": "Clean intact white shirt"},
    {"id": "ventura_incident", "description": "Blood on shirt in the incident scene"}
  ]
}}
```

Assign `characters=["ventura_clean"]` in ordinary scenes and the other state only
where intended. A selected state's reference image takes precedence over the
entity reference. MCP-created scenes and `sb_set_scene_entities` use explicit
assignments: mentioning a name in prose does not automatically insert its portrait
or another appearance state. Store people mentioned but not depicted in
`mentioned_entities`, separate from visible character assignments.

## Scenes and montage

Minimal scene for creation/import:

```json
{"scene_id": "001", "start_seconds": 0, "duration_seconds": 6,
 "narration": "The witness entered the courtroom.",
 "prompt": "A wide view of a quiet courtroom, the witness at the doorway",
 "characters": ["ventura_clean"], "locations": [],
 "video_prompt": "Slow camera move toward the doorway"}
```

Additional fields: `objects`, `groups`, `era_state_id`, `shot`, `motion`,
`motion_in`, `motion_out`, `transition`, `video_frame_role` (`start`, `end`, `none`),
`video_duration_seconds`, `video_audio_enabled`, `video_audio_volume`, and
`generation_overrides` (`style`, `seed`, `reference_images`, `prompt_mode`,
`raw_prompt`). Reference entries use `path`, `label`, `kind`.

`sb_batch_update_scenes` takes an `operations` list:

```json
[{"operation": "create", "scene": {"scene_id": "001", "start_seconds": 0,
  "duration_seconds": 6, "prompt": "A courtroom"}},
 {"operation": "update", "scene_id": "002", "scene": {"video_prompt": "Slow pan"}}]
```

The batch commits only if every operation validates. Deletion uses
`{"operation":"delete","scene_id":"001"}`. Split retains total duration;
merge retains the first scene's media/prompt and joins narration. Reorder requires
every scene exactly once and recalculates visual times; it never edits source
audio. `sb_update_timeline` changes renderer options such as `fps`, `crf`,
`transition_seconds` and `zoom_percent`. Image resolution/provider settings are
selected through `sb_set_generation_profile` overrides.

## Providers, assets and execution

`sb_get_capabilities` returns configured profiles and public provider settings.
`sb_set_generation_profile` selects `local`, `custom_comfyui`, `litellm` or
`runpod`, with project-specific overrides. Credentials stay in application
settings and are not returned through these tools. Missing provider configuration
causes an actionable job error, not a UI dialog or silent provider substitution.

`sb_generate_frames`, `sb_edit_frames`, `sb_generate_videos` accept explicit
`scene_ids`. `assign=false` retains candidates for inspection. Reference tools
take `entity_id` and optionally `state_id`. `sb_edit_video` trims and concatenates
ordered time ranges from a scene's existing clip. `sb_extract_video_frame` returns
an image asset for a specified time; preview that candidate by its asset ID.
`sb_preview` renders at most 24 images per contact sheet; select scenes to paginate.

Generation, references and rendering reuse the application's provider and FFmpeg
implementations. Provider-specific limitations still apply (accepted references,
video lengths, image sizes, start/end frames). There is no claim that every model
supports every mode. Final output is written as a new asset; prior files remain.

## Tool index

- Source/project: `sb_list_projects`, `sb_create_project`, `sb_get_project`,
  `sb_update_project`, `sb_set_source`, `sb_get_timed_text`, `sb_search_timed_text`,
  `sb_import_timed_text`, `sb_export_timed_text`.
- Styles: `sb_list_styles`, `sb_set_style`.
- Entities: `sb_list_entities`, `sb_get_entity`, `sb_create_entity`,
  `sb_update_entity`, `sb_delete_entity`, `sb_find_entity_usages`, `sb_create_state`,
  `sb_update_state`, `sb_delete_state`, `sb_set_scene_entities`,
  `sb_generate_reference`, `sb_edit_reference`, `sb_set_reference`.
- Scenes: `sb_list_scenes`, `sb_get_scene`, `sb_create_scene`, `sb_update_scene`,
  `sb_batch_update_scenes`, `sb_delete_scene`, `sb_split_scene`, `sb_merge_scenes`,
  `sb_reorder_scenes`, `sb_update_timeline`, `sb_compile_scene_prompt`,
  `sb_import_prompts`, `sb_export_prompts`.
- Media: `sb_get_capabilities`, `sb_set_generation_profile`, `sb_generate_frames`,
  `sb_edit_frames`, `sb_generate_videos`, `sb_edit_video`, `sb_import_asset`,
  `sb_assign_asset`, `sb_list_assets`, `sb_preview`, `sb_extract_video_frame`, `sb_render`.
- Control: `sb_validate`, `sb_get_change_impact`, `sb_get_job`, `sb_list_jobs`,
  `sb_cancel_job`, `sb_retry_job`, `sb_create_snapshot`, `sb_restore_snapshot`.

Restart the stdio client and an **idle** EngineHost after installing this update.
An already running older host will not discover new routes until restarted.
