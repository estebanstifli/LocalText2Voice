# Historical periods in Video Storyboard

The conversational analyzer supports historical periods independently of Scenes
only, Basic continuity and Full continuity. There is one analysis pipeline;
the old Compact/Simple processes and their historical-period setting are removed.

## Choose the setting before analysis

- **Manual:** enter an optional fixed year/era for the whole audiobook. Empty means
  no historical restriction; it does not mean present day.
- **Automatic, one period:** identify a common visual setting. If genuinely
  different periods are identified, show a warning rather than force one over
  the whole book; rerun with multiple periods or enter a manual setting.
- **Automatic, multiple periods:** follow the setting represented in each passage,
  including flashbacks and returns to previously identified periods.

The manual text is preserved when automatic mode is selected, but is not sent as
an active instruction. Existing projects keep manual behavior until explicitly
reanalyzed. Image files and edited prompts are not rewritten by this feature.

## Detection and review

1. A plain-text report identifies short period names (e.g. Neolithic, Bronze Age,
   Victorian era, 1920s), without regions, environments or narrative summaries.
   Separate visual context describes period-specific clothing, architecture and technology,
   distinguishing explicit evidence from broad inferences. Merely mentioning a
   date is not sufficient to change the represented setting.
2. Optional review exposes **Periods and visual context**. Correct descriptions,
   merge duplicate periods, and retain literal opening sentences for timing.
3. A separate JSON request structures the reviewed reports. The application
   validates source quotes, creates canonical period IDs, and maps changes to
   narration cues. Ambiguous/missing anchors produce warnings, not guessed dates.
4. Period boundaries split visual intervals before their prompts are written.
   Each interval receives its own period; extra visual context is optional. Unknown settings
   stay unrestricted; they never inherit the global list of detected periods.

Timing is cue-level, not word-level. A change within a cue is applied at that
cue's boundary. Historical inference remains a model judgment and can be reviewed.

## Inspect and correct results

The **Eras / periods** sidebar card displays progress and counts. The final
**Era appearances** tab lists periods, scene counts and visual context. Double-click
a period there or in the project's **Historical periods** tab to:

- Edit its name and separate visual context.
- Browse its scene thumbnails, including frames not generated yet.
- Assign/unassign scenes explicitly.
- Merge it into another period, using the destination period's context.

The default image prompt contains only the period's name:

```text
ERA: Bronze Age.
```

Enable **Include visual context**, to the right of the era field in **Project visual
direction → Historical periods**, to add a separate instruction:

```text
ERA: Bronze Age. ERA VISUAL CONTEXT: Wool tunics and bronze tools.
```

This checkbox defaults to off, is saved per project, and applies to batch generation
and individual regeneration. Present-day passages add neither instruction, even
with the checkbox enabled. Their boundaries are still tracked so a previous
historical setting stops applying. Existing manual raw prompts remain unchanged.
Existing records use their saved name (or description when no name exists);
reanalyze or edit their names to simplify previously generated regional labels.

The period also informs discovery/profile conversion and each scene's visual-description
request. Existing custom prompts remain user-owned. Reference images should be
reviewed for period-appropriate clothing, architecture and technology; no separate
era reference images are generated automatically.

## Storage and compatibility

`continuity.eras` contains reusable identities and visual descriptions.
`continuity.era_assignments` contains their timed occurrences, including unknown
gaps; a period can appear more than once. Scenes store `era_state_id` and `era`.
The project stores the selected mode, reports, evidence and request configuration.
Changing the mode invalidates an incompatible review checkpoint.

Existing era records with a single from/to interval remain readable. Editing
general style or audio controls does not replace automatic period assignments.
An explicit change to the project's fixed era applies a manual override globally.

Validation covers all three continuity plans, recurrence, unknown gaps, conflicting
single-period analysis, quote validation, pause/resume, edits/merges, persistence,
prompt compilation and UI controls. LLM tests use simulated responses.
