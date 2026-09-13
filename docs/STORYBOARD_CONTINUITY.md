# Storyboard continuity (ledger v3)

## Current combined-report flow (2026-09-11)

The default medium input allowance is 17,000 characters. Source passages are
balanced, preferring paragraph and sentence boundaries, with no text loss or
overlap. A roughly 50,000-character book uses three passages; shorter books may
use one and longer books use more. Ollama retains its 8,192-token default context.
The analysis dialog remains editable; the previous saved 12,000 default upgrades
to 17,000, while other explicit limits remain unchanged.

Each passage receives the tested B question: characters and appearance, changes,
and a short story summary in free-text sections. There is no separate appearance
chat. After all reports, short pairwise novelty requests append new character
descriptions to the first report's character section. Initial descriptions are
not rewritten by the novelty step. Explicit no-new-character replies are not
converted into profiles. This does not guarantee semantic alias deduplication.

Scene proposals use independent source-passage requests, avoiding carrying the
entire combined answer into another large request. Place summaries, scene quote
alignment, JSON conversion and rendering remain downstream. Character conversion
receives accumulated initial descriptions, never the concatenation of later
appearance-change sections. Full analysis still verifies changes against their
original passage and builds timestamped states; basic analysis keeps baselines.

Project-local `storyboard/analysis/<run>/` stores `resumenN.txt`, `novedadesN.txt`,
`resumen_unificado.txt` (append-only character list), `historia_concatenada.txt`
and `cambios_por_tramo.txt`. Raw reports and per-passage summaries also persist
in the plan. Existing analysis checkpoints are invalidated by pipeline revision.
The new prompt keys are `conversation_report` and `conversation_additions`;
legacy custom questions remain stored but are not applied to this new phase.

## User-controlled maximum scene duration

Scene-first analysis uses `scene.maximum_seconds` (default 20, range 4–60).
An AI-proposed scene of duration D is split into ceil(D / maximum) equal shots;
appearance-state boundaries can split these further. There is no longer a fixed
20/10-second rule. Legacy minimum/target fields remain for compatibility but are
hidden in settings and cannot increase the maximum during normalization.

## Explicit character changes and human hair (2026-09-10)

The previous appearance chat chose one visual design, including hair length
and color (or baldness) for humans only. Source facts take priority; missing hair
details were chosen once, not once per frame. The current B question is shorter
and does not explicitly require hair attributes. The two-field profile converter
preserves that INITIAL portrait rather than mixing later outfits into it.

Full analysis subsequently extracts explicit appearance changes from bounded
original passages with their discovery summaries. Basic analysis keeps one fixed
portrait; scene-only analysis still skips characters entirely. Changes must name
an existing character, have an allowed visual kind, and cite a unique original
sentence. Unknown identities, ambiguous quotes and invalid rows produce warnings,
not guessed state boundaries. Actions, moods and newly revealed unchanged facts
are excluded by the extraction instructions; semantic accuracy still depends on
the LLM and can be corrected in Characters.

The application maps each accepted quote to its narration cue (already offset),
orders changes by source position and merges changes sharing a cue. Only actual
changes require a further small portrait-update request: preserve unaffected
traits, replace superseded ones. State descriptions are complete replacements,
not accumulated prose. Boundaries use seconds, not scene indices, and split visual
intervals before prompt generation. Timing inside a cue is approximate and logged.
Repeated source quotes are deliberately left for manual review. Transport errors
and cancellation are not hidden as continuity warnings.

## Safe entity-name resolution (2026-09-10)

`storyboard_entity_names.py` resolves exact IDs/names/aliases first, then unique
normalized variants (case, punctuation, leading "The", trailing parenthetical
qualifiers). It does not fuzzy-match a generic word such as "Pig" to an individual.
After profile conversion, unambiguous qualified display names are shortened while
IDs and original-name aliases remain intact. Colliding short names stay qualified.

Unresolved scene references emit warnings. Up to six references at a time are
sent to a small fallback request containing only their narration and candidate
IDs/names. Results must select an allowed candidate or abstain; unknown IDs and
duplicate answers are rejected. Contextual references are never stored as aliases.
Candidate sets above 24 are left for manual review, rather than guessed.

The same deterministic matcher handles explicit names in manual image prompts
and existing projects, respecting active states and an already selected state.
Raw prompt overrides bypass composition as before. Project3 scene 36 was checked
read-only: its short pig name now includes the stored yellow-shirt/overalls profile.

## Order-preserving approximate scene starts

An invalid quote is repaired with its next neighbour, using at most twelve
source sentences bounded by the surrounding anchors. This permits correcting
one-scene-shifted quotes without changing scene titles/order or resending the book.
If repair still fails, the planner retains the longest increasing chain of exact
anchors and interpolates remaining starts between them. Shared timing cues can
receive fractional starts. These are estimates, not ASR word timestamps.
The original quote, `alignment_method` and `alignment_approximate` are saved;
approximations emit warnings instead of aborting otherwise usable scene plans.
Absent scenes or unusable narration timings still fail explicitly.

Regression verified with the Ana/Peter log from 2026-09-09 20:43:46: Qwen repaired
the gulls/boats pair to 63.899s and 82.589s; all eleven scenes aligned in their
original order. The deterministic fallback was also tested independently.

## Empty visual-response recovery

Image-description schemas require exactly the requested number of nonempty
descriptions. A successful JSON response with a missing/wrong-count list is not
treated as a connection error: the planner retries each interval individually,
with at most two attempts per missing description. Correct-count batches retain
valid rows and retry only invalid positions. Wrong-count batches are discarded
because their positional correspondence is ambiguous. Warnings appear in the
normal progress log; prior completed scenes are untouched. Cancellation and
provider errors still propagate, and exhausted retries remain explicit errors.

Regression probe: `tools/test_storyboard_empty_visual_recovery.py <analysis-log>`.

## Small summary converters (2026-09-09, revision 2)

The three initial chat questions are unchanged. After them, a separate short
place report is extracted from the scene answer, without the source book or chat
history. Review exposes characters, appearance, places and scene proposals.

Each conversion is independent and receives only its own summary:

- Characters: the character and appearance answers → `characters` rows containing
  only `character` and `visual_description`.
- Places: the place answer → `locations` rows containing only `location` and
  `visual_description`.
- Scenes: the proposed scenes answer → titles and unchanged start quotations.

No original book, existing ledger, timestamps, aliases or era task are sent to
these converters. Summaries are bounded to 6,000 characters per conversion.
The application creates IDs and one editable baseline state spanning the book;
this defines appearance, NOT visibility. Scene bindings control presence. Repeated
names do not accumulate conflicting descriptions or become invented changes.
This simplified conversion no longer extracts temporal changes or automatic eras;
manual states and the project's supplied era remain supported. Those richer
analyses must be separate refinements, not added back into these converters.

Quote validation is local. A failed quote gets at most one small repair request
with up to eight candidate source sentences, not the entire book. Scene prompt
requests use local story context and canonical-name lists rather than the entire
appearance ledger. Old review checkpoints are invalidated by pipeline revision.

Live converter probe: `tools/test_storyboard_summary_conversion.py`.

## Active scene-first pipeline (2026-09-09)

The public `plan_video_storyboard` now runs `storyboard_conversation.py`.
The earlier deterministic planner is retained privately for helper/legacy tests,
but is no longer selected by the application. Historical sections below describe
that previous pipeline.

1. Plain-text chat: characters + brief summary; proposed visual scenes **with
   original starting sentences**; character appearance. Shared user/assistant
   history, thinking enabled, model sampling defaults. No timing markup or JSON
   in these three questions. Scene-only mode asks only the scene question.
2. Optional editable review, with the existing save/pause/resume checkpoint.
3. Transcribe profiles and scene proposals to application JSON. Basic mode uses
   initial profiles only; full mode permits source-anchored physical changes.
4. Match quotes literally, allowing case/punctuation differences. Reject
   ambiguous/out-of-order anchors after one repair request; do not silently sort.
   Match to narration-cue time (not word-level ASR). The first image starts at 0;
   cue times already include the voice offset.
5. Scenes over 20 seconds receive approximately 10-second image assignments.
   A separate visual-description request receives the narration for each interval,
   retaining the AI-proposed parent scene. Appearance changes also split intervals.
   No images are generated by analysis.

Progress, raw requests/responses, reports and partial plans use the existing worker
and persistence callbacks. Settings exposes the three new editable questions.
Previous customized legacy prompts are retained but not used for these questions.
New default input batches are 12,000 characters (saved project limits still win).
Longer sources use consecutive unchanged source slices rather than timing JSON.
First-appearance corrections inferred from explicit scene bindings are logged and
retained in state metadata; they never move subsequent appearance states.

Validation: `tests/test_storyboard_conversation.py`; live isolated experiment:
`tools/test_storyboard_scene_first.py`. The 8K-context Qwen3:8b run produced
9 proposed scenes / 41 image plans, 8 characters and 4 locations. This verifies
execution and alignment, not perfect semantic interpretation; review remains useful.

## Conversational discovery and thinking (2026-09-09)

Ollama storyboard requests now always send `think: true`, for both free text
and structured calls, including retries. Discovery uses a single user message
with a short English question asking which characters appear and a 2–3-line synopsis,
followed by clean narration (no timing JSON). Saved custom character instructions
still take precedence over the default question.

The synopsis is retained as `continuity.story_context` and injected as orientation
in later LLM calls, explicitly not as evidence for current-unit cast or timing.
For multiple narration blocks, pairwise short-summary consolidation covers the
whole story without silently dropping later blocks. The synopsis is editable in
the review dialog and checkpointed. Scenes-only still skips character discovery.
The raw character answer is retained separately from its synopsis.

## Content plans and review checkpoint

The Analyze audiobook preflight dialog now selects a per-project content plan:

- Scenes only: skips discovery, continuity structuring and unit binding. The
  existing deterministic narration timing, scene ranges, style and coverage stay.
- Basic continuity: stable initial character/place profiles, without temporal
  appearance states or automatic era detection. A manual project era is kept.
- Full continuity: retains the existing temporal continuity pipeline.

These are independent of Compact versus Simple tasks (the provider task strategy).
The default remains Full for compatibility. The preflight review checkbox defaults
off. With review enabled, free-text summaries are prepared before structuring;
Scenes only adds a visual-summary pass, respecting the input block size.

The worker genuinely waits for GUI review without making further model calls.
The editable tabs preserve original and revised text separately. Revised sections
are explicitly prioritized in downstream prompts, while original narration units
remain the source for timing. This prioritization is an LLM instruction, not a
guarantee that a model will follow every correction. Unchanged whole-book reports
are not appended to every request.

`storyboard/analysis_review.json` stores content choices, input/output limits and
an atomic, autosaved review checkpoint, without API credentials. Continue approves
the draft; Save and close ends the worker at the checkpoint; Cancel ends analysis.
Reopen Analyze audiobook to resume a matching draft. Changed narration/timing or
analysis configuration invalidates reuse. Completion marks the checkpoint done.
No existing project or generated image is migrated by this feature.

## Configurable analysis

Settings → Video Storyboard → Storyboard LLM provider opens a Continuity
analysis modal (no additional Settings tab). Settings save automatically;
the nested instruction editor retains its explicit Save/Cancel actions.
Its compact process preserves
the joint discovery/structuring stages. Simple tasks revision 2 uses
character-only plain-text discovery, a minimal identity/group registry, and
brief complete visual profiles. Places independently follow the same
report → registry → profiles sequence. Era, binding and scene generation follow.
Known entities are supplied only to their relevant stage. This is an opt-in
strategy, independent of Ollama versus LiteLLM; it is not a guarantee of model
accuracy.

Small/medium block defaults are 2,000/4,000 characters; a custom limit is
available. The preflight analysis modal can override the limit for a run.
Project-provided era mode suppresses automatic era detection. A supplied
project era is retained in either mode.

The instruction editor customizes stages of the selected process; it does not
reorder dependencies or edit provider JSON schemas. Save applies changes,
Cancel discards them, and each stage can restore its actual code default.
The preview is explicitly illustrative; actual requests remain in raw logs.

Every partial/final continuity ledger stores analysis_configuration: version,
effective instructions, protected contracts, process, model, and limits.
Credentials are excluded. Editing Settings does not rewrite existing projects.

Simple tasks no longer expose the six typed character appearance slots to the
provider. Responses contain definitions for new names and optional changes
and revelations arrays; an empty array means no updates. Each update contains
the complete resulting short profile and a verbatim quotation, checked against
the original unit. The raw passage accompanies the reports, so structuring
does not have to trust an earlier summary blindly. Up to five referenced
entities share a profile request; an empty registry skips that request.

The adapter assigns new IDs, preserves existing identities and aliases, and
converts valid updates into ledger events. Initial profiles are stored once as
the identity. Changed profiles override the old appearance for their interval,
instead of concatenating the two. Revelations update the active profile without
creating a new period. Manual edits remain authoritative. Source reports are
retained in the ledger and complete requests/responses remain in raw logs.
The compact/GPT request path and its typed appearance protocol are unchanged.

Analysis keeps four phases: free-text discovery, continuity structuring,
unit binding, and scene visuals. Optional appearance completion only bootstraps
a **new** incomplete entity. Empty appearance fields on an existing entity
mean "no new visual facts", not "design it again".

## Contracts

- The LLM interprets source language, pronouns, collective introductions,
  revelations and actual changes. Identity is summarized into 3–5 distinctive
  visual anchors (target: 25 words), not every fact in the discovery report.
  Normal mobility is never requested; aids require source support.
- Generated identity storage also retains at most five whole visual clauses
  with a 35-word selection budget (a single indivisible longer clause is kept).
  Source-backed traits outrank invented ones; manual/legacy text is untouched.
- Character events have typed appearance slots: age, wardrobe, grooming,
  body, equipment and form. Places have structure, condition and decoration.
  Values are brief English visual descriptions (target: 30 words total).
  Null means no update; an empty string explicitly clears a slot.
  An update supplies the complete new value of that slot.
- Actions, achievements, relationships, occupancy, hunger, mood and passing
  expressions are not temporal appearance states. Building a house changes
  the location, not its builder. Construction, completion, damage and
  restoration replace previous conditions rather than accumulating.
- Code resolves IDs against the ledger AND new entities in the same response,
  then processes events chronologically. Literal name
  matching may move a first appearance earlier, never later than a valid
  group/pronoun introduction supplied by the LLM.
- `identity_description` and state `description` remain editable text. Internal
  `identity_traits` retain provenance; `visual_traits` store typed appearance
  slots, their origins and a matching text snapshot. Both are persisted.
- Groups of individually tracked characters have membership timelines. The
  binder may select a group and code expands only its current members. A
  collective introduction can establish those members' earlier appearances;
  later joiners are not backdated to the group's original introduction.
  Unknown membership produces a warning, not guessed characters. Anonymous
  crowds without tracked individuals may retain a collective visual design.
- Canonical IDs take precedence. Ambiguous shared aliases do not silently
  select the first character.
- Later events do not append entire rewritten identities. Source-backed
  revelations may replace inferred traits; source contradictions are retained
  conservatively unless the event describes a real change. A verified source
  quotation on an actual delta event supports translated evidence too; repeated
  first appearances cannot use that fallback to redesign an existing entity.
- Explicit changes copy the previous state and update affected slots only.
  Empty/identical deltas do not create states. Full bodily transformations
  use the form slot instead of an incompatible baseline identity; clearing
  that slot restores the baseline.
- Completion only bootstraps a missing identity. It receives the entity's
  canonical name, context and source evidence, not just an opaque ID.
- Old free-text replies use a conservative compatibility parser. Arbitrary
  activity is not accepted merely because it has a valid source quotation.
  The new contract does not use English/Spanish source-keyword heuristics
  to decide whether an evolution occurred. No additional summary call is added.
- This is a constrained semantic contract, not proof of factual truth. A model
  can still misclassify a trait or group; manual correction remains available.
- Editing text invalidates its old inferred metadata. Manually authored
  records are not rewritten by analysis merges.

## Image prompt

Only the selected entities/states are compiled. The generated prompt has a
short visual view of each identity/state, deduplicated by trait, with the
selected state taking precedence. Default mobility boilerplate is removed;
real aids are retained. Raw prompt overrides bypass this compiler entirely.

No automatic rewrite of stored projects or generated images is performed.
Old accumulated definitions get a conservative compact view during generation,
but re-analysis is needed to rebuild their factual ledger and timing anchors.
Re-analysis uses the existing confirmation before replacing scenes.

Regression tests: `tests/test_video_storyboard_appearance.py`,
`tests/test_video_storyboard_visual_traits.py`, plus the
planner, ComfyUI compiler, and project persistence suites.
