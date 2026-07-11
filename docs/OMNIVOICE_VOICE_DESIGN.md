# OmniVoice Voice Design

OmniVoice voice design does not accept arbitrary natural-language prompts.
The `instruct` value must use a controlled vocabulary validated by the
`omnivoice.utils.voice_design` module.

## Supported English Items

Use comma-separated items. Each category accepts at most one value.

| Category | Supported values |
| --- | --- |
| Gender | `male`, `female` |
| Age | `child`, `teenager`, `young adult`, `middle-aged`, `elderly` |
| Pitch | `very low pitch`, `low pitch`, `moderate pitch`, `high pitch`, `very high pitch` |
| Style | `whisper` |
| Accent | `american accent`, `british accent`, `australian accent`, `chinese accent`, `canadian accent`, `indian accent`, `korean accent`, `portuguese accent`, `russian accent`, `japanese accent` |

Examples:

```text
female, young adult, moderate pitch
male, middle-aged, low pitch, british accent
female, child, high pitch
male, elderly, very low pitch
female, young adult, whisper, american accent
```

## Important Rules

- Use `middle-aged`, not `middle aged`.
- Do not send descriptive words such as `calm`, `relaxed`, `warm`, `steady`,
  `joyful`, or `storyteller` directly to OmniVoice.
- LocalText2Voice style cards may display friendly descriptions, but they must
  map internally to supported OmniVoice items.
- Do not combine multiple pitch values in one instruction.
- English accents and Chinese dialects cannot be mixed in one instruction.

## Supported Non-Verbal Tags

These tags are written directly in the synthesis text, not in `instruct`:

```text
[laughter]
[sigh]
[confirmation-en]
[question-en]
[question-ah]
[question-oh]
[question-ei]
[question-yi]
[surprise-ah]
[surprise-oh]
[surprise-wa]
[surprise-yo]
[dissatisfaction-hnn]
```

## UI Mapping

The OmniVoice Voice Designer exposes only the parameters OmniVoice supports:
gender, age, pitch, whisper, and accent. The generated audio request is a
comma-separated `instruct` string built from those selected values.
