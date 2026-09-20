# Storyboard continuity (ledger v3)

## Conversational analysis

`plan_video_storyboard` runs `storyboard_conversation.py`. The earlier
Compact/Simple planners have been removed. Old saved process settings normalize
to conversational analysis; old project ledgers and rendered assets remain readable.

The default medium input allowance is 17,000 characters; small is 4,000 and a
custom limit is available. Source passages are balanced at paragraph/sentence
boundaries without dropping text. Per-run limits are editable before analysis.

1. If automatic periods are enabled, discover their names, regional visual
   context and source anchors in plain text. Manual periods skip these requests.
2. Discover selected characters with their initial appearance, changes and a
   short story summary. Pairwise novelty requests append new identities without
   rewriting previously selected designs. Reuse known object/location identities
   through their separate discovery and unification passes.
3. Propose scenes with literal opening sentences in bounded source excerpts.
   Discover selected locations and important objects.
4. Optionally pause for editable review. Original and revised reports are kept
   separately; no further provider work starts until the user continues.
5. Structure reviewed periods, profiles and scene proposals with separate JSON
   contracts. Full continuity additionally verifies explicit character changes
   against the original passage and creates complete replacement appearance states.
6. Align source anchors locally to narration cues. Divide intervals at the
   configured maximum scene duration (default 15 seconds, range 4–60), character
   appearance changes and period boundaries.
7. Request concise English image descriptions, at most two intervals at a time,
   with each interval's narration, canonical entity names and applicable ERA.
   Only entities visible in that interval are bound to the resulting scene.

Scenes only defaults to no character/location profiles. Basic retains stable
baseline appearances. Full supports explicit physical changes. Historical-period
mode is independent of all three plans. See [Historical periods](HISTORICAL_PERIODS.md)
for detection, review, recurring periods and manual correction.

## Source alignment and validation

Timing belongs to the application. Literal quotes allow case/punctuation
normalization. Invalid scene anchors receive a bounded repair attempt; a remaining
scene alignment failure uses an explicitly flagged order-preserving approximation.
Period/change anchors that are absent or ambiguous produce warnings and are not
used to invent boundaries. Cue times already include the voice offset. A quote
inside a narration cue is aligned to the cue boundary, not to word-level ASR.

The first image covers the opening silence. An omitted opening passage is kept
as its own scene rather than pulling a later action backwards. Visual requests
receive only the narration applicable to their interval. Wrong-count or empty
visual replies are retried per frame, with a bounded retry count. Transport,
provider and cancellation errors propagate without discarding completed scenes.

## Identity and appearance

Baseline profiles define reusable appearance, not presence. Scene bindings decide
who or what is visible. Source descriptions take priority; the character converter
chooses missing hair/clothing once as a consistent initial design. Full mode
extracts actual clothing, age, hair and lasting physical changes; actions, moods,
possessions and newly revealed unchanged facts are not appearance states.

Names resolve against canonical names and aliases. Repeated entities do not
accumulate contradictory profiles. Identity resolution remains an LLM judgment;
manual entity and scene editing is available when the model merges or misses names.

## Review, logging and persistence

`storyboard/analysis_review.json` stores project choices, limits and an atomic
review checkpoint without credentials. Continue approves it; Save and close
pauses work; Cancel ends it. Changed source, timing, mode or analysis settings
invalidate incompatible checkpoints.

`storyboard/analysis/<run>/` stores character reports and unification summaries.
Discovery reports, historical-period evidence and edited summaries also persist
in the plan. Raw request/response logs expose what the provider actually received.
Input and output token totals reflect usage reported by the provider and are
marked partial when usage is unavailable.

Partial/final plans store the effective conversational configuration, model,
limits and selected content/era mode. Existing projects are not automatically
reanalyzed or regenerated. Optional character-reference generation runs after
analysis; historical periods do not automatically create reference images.

## Settings and image prompts

Settings offers the conversational block size and editable discovery instructions:
characters/report, new characters, scene proposals and historical periods. JSON
contracts and stage dependencies remain application-owned. Manual versus automatic
periods is configured before analysis, not by an obsolete global detection setting.

The image compiler combines scene action, selected profiles, composition, visual
style and `ERA: <description and visual context>`. An unknown automatic scene does
not inherit a global summary of other periods. Explicit custom prompts are kept
unchanged. Reference images still need to agree with the represented clothing,
architecture and technology; image models may otherwise follow the reference.

The implementation is covered by conversational, period, character-change,
entity-resolution, prompt, persistence and UI tests. Simulated provider responses
verify application behavior, not a guarantee of historical or semantic accuracy.
