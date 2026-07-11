# LocalText2Voice Markup

LocalText2Voice Markup, or LTV Markup, lets you add narration instructions inside your source text using double braces.

Example:

```text
The house had been empty for years.
{{pause.long}}
I do not like this place.
```

Markup is optional. If your text does not contain `{{...}}` commands, LocalText2Voice works exactly as before.

## Basic Syntax

Commands use double braces:

```text
{{command}}
{{command value}}
{{command "value with spaces"}}
{{command arg1 arg2}}
{{command.subcommand}}
```

Command names are case-insensitive. These are equivalent:

```text
{{voice "Lucia"}}
{{VOICE "Lucia"}}
{{Voice "LUCIA"}}
```

Voice matching is also case-insensitive and flexible when LocalText2Voice can
map the name to an installed voice for the selected engine. Exact matches are
preferred, but short fragments are accepted:

```text
{{voice "edu"}}
```

If the selected engine has a voice named `Eduardo - es`, the app can select it
and write a warning in the log so you know an approximate match was used.

Quoted values are supported:

```text
{{voice "Maria"}}
{{chapter "Chapter 1"}}
{{alias "GPT" "gee pee tee"}}
```

Unknown or malformed commands do not stop generation. They are ignored and written as warnings in the log.

## Command Compatibility

`*` means the command is usable with that engine in the current LocalText2Voice implementation.

Some commands are handled by LocalText2Voice before the text is sent to TTS. Those commands work with every engine because the TTS model never sees them. Engine-specific commands are only marked where the selected backend actually receives and uses the instruction today.

| Command | Piper | Kokoro | Chatterbox | Qwen3 TTS | OmniVoice | OpenAI | ElevenLabs | Gemini | Azure | Custom HTTP | Notes |
| --- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | --- |
| `{{pause ...}}` | * | * | * | * | * | * | * | * | * | * | App inserts real silence between segments. |
| `{{chapter "..."}}` | * | * | * | * | * | * | * | * | * | * | App creates named internal groups before generation. |
| `{{alias "A" "B"}}` | * | * | * | * | * | * | * | * | * | * | App replaces later text before TTS. |
| `{{reset}}` | * | * | * | * | * | * | * | * | * | * | App resets active markup state. |
| `{{voice "..."}}` | * | * | * | * | * |  |  |  |  | * | Switches installed/local voices, Qwen aliases, OmniVoice reference voices, or custom template voice value. |
| `{{lang ...}}` |  | * | * | * | * |  |  |  |  | * | Custom HTTP receives the literal language value in `{{language}}` / `{{lang}}`. |
| `{{speed ...}}` | * | * | * | * | * | * | * | * | * | * | App can apply speed after generation with FFmpeg when the engine has no native speed control. |
| `{{cmd ...}}` | * | * | * | * | * | * | * | * | * | * | App attaches TTS parameters to the next segment only. Engines ignore unsupported keys. |
| `{{preset ...}}` | * | * | * | * | * | * | * | * | * | * | App attaches TTS parameters to every following segment until `{{reset.preset}}`. |
| `{{sendcommand ...}}` | * | * | * | * | * | * | * | * | * | * | Alias of one-shot `cmd`. |
| `{{sendcomand ...}}` | * | * | * | * | * | * | * | * | * | * | Tolerant misspelling alias of `sendcommand`. |
| `{{volume ...}}` | * | * | * | * | * | * | * | * | * | * | App applies voice gain or loudness normalization after generation with FFmpeg. |
| `{{music "..."}}` |  |  |  |  |  |  |  |  |  |  | Parsed/reserved for future markup timeline mixing. |
| `{{music.stop}}` |  |  |  |  |  |  |  |  |  |  | Parsed/reserved for future markup timeline mixing. |
| `{{music.volume ...}}` |  |  |  |  |  |  |  |  |  |  | Parsed/reserved for future markup timeline mixing. |
| `{{sfx "..."}}` |  |  |  |  |  |  |  |  |  |  | Parsed/reserved for future sound effects. |
| `{{mark "..."}}` |  |  |  |  |  |  |  |  |  |  | Parsed/reserved for future editing/navigation markers. |

Recommended mental model:

- App-level commands shape the audiobook timeline and are safe with every TTS engine.
- Voice/language commands are chunk boundaries; LocalText2Voice will not mix two voices or languages in one request.
- Qwen and other engines with request parameters should use `{{cmd ...}}` for one segment or `{{preset ...}}` for persistent parameters, not raw `[tags]` unless the selected model explicitly documents bracket tags.

## Pauses

Pauses are rendered as real silence between audio segments.

```text
{{pause 700}}
{{pause 700ms}}
{{pause 0.7s}}
{{pause.short}}
{{pause.medium}}
{{pause.long}}
{{pause random 500 1200}}
```

Preset values:

| Command | Silence |
| --- | ---: |
| `{{pause.short}}` | 300 ms |
| `{{pause.medium}}` | 700 ms |
| `{{pause.long}}` | 1200 ms |

Example:

```text
This is the first idea.
{{pause 900ms}}
Now the second idea has more room to breathe.
```

## Voices

Voice commands switch the active voice for the following narration segment. A
voice or language command creates a safe boundary before chunking, so the app
does not mix two different voices inside the same TTS request.

```text
{{voice "Maria"}}
{{voice.default}}
{{voice.narrator}}
{{voice.character "Lucia"}}
```

Example:

```text
{{voice.narrator}}
The corridor was silent.

{{voice.character "Lucia"}}
Who is there?
```

Current behavior:

| Engine | Behavior |
| --- | --- |
| Piper | Looks for an installed Piper voice by name, id, or language/name fragment |
| Kokoro | Looks for a Kokoro voice id or display name |
| Chatterbox | Looks for an installed reference voice by display name or filename |
| Qwen3 TTS | Looks for a Qwen speaker by id/display name, or a UI-style `Speaker - Language` alias such as `Serena - Spanish` |
| Custom HTTP | Sends the literal value to the custom engine config as `voice`, so templates can use `{{voice}}` |

If a voice is not found exactly, LocalText2Voice tries to find the closest
compatible voice for the selected engine. When an approximate match is used,
the log includes a warning similar to:

```text
LTV Markup warning: voice "edu" was not found exactly. Closest voice selected: Eduardo - es
```

If no compatible voice can be found, the app writes a warning and keeps using
the voice selected in the UI.

Qwen note: the UI displays combinations such as `Serena - Spanish`, but Qwen
internally receives two separate values: `speaker=Serena` and
`language=Spanish`. These combinations are accepted in markup:

```text
{{voice "Serena - Spanish"}}
{{voice "Serena – Spanish"}}
```

## Speed

Speed commands are parsed as active narration state. If the selected engine
supports speed natively, the engine can receive it directly. Otherwise
LocalText2Voice post-processes the generated WAV with FFmpeg, so the command is
usable across engines.

```text
{{speed 0.9}}
{{speed.slow}}
{{speed.normal}}
{{speed.fast}}
```

Preset values:

| Command | Speed |
| --- | ---: |
| `{{speed.slow}}` | 0.85 |
| `{{speed.normal}}` | 1.0 |
| `{{speed.fast}}` | 1.15 |

## Volume

Volume commands adjust the generated narration audio after TTS. They are useful
when one character, engine, or paragraph sounds louder than the rest.

```text
{{volume 0.8}}
{{volume 80%}}
{{volume -3db}}
{{volume.db -3}}
{{volume.normalize -16}}
{{volume.lufs -16}}
{{volume.normal}}
```

Supported forms:

| Form | Meaning |
| --- | --- |
| `{{volume 0.8}}` | Multiplier; `0.8` is converted to about `-1.94 dB` |
| `{{volume 80%}}` | Percentage multiplier |
| `{{volume -3db}}` | Direct gain in dB |
| `{{volume.db -3}}` | Direct gain in dB |
| `{{volume.normalize -16}}` | Normalize the segment to `-16 LUFS` |
| `{{volume.lufs -16}}` | Alias for LUFS normalization |
| `{{volume.normal}}` | Reset volume processing |

## TTS Parameters: One-Shot And Persistent

`cmd` attaches parameters to the next segment only. `preset` attaches
parameters to every following segment until it is changed or reset.

```text
{{cmd
"instruct": "Say this sentence with surprise.",
"temperature": 0.7,
"top_p": 0.9
}}

Only this sentence uses the one-shot parameters.
```

`sendcommand` and the tolerant misspelling `sendcomand` are accepted as aliases
of one-shot `cmd`.

Use `preset` for a style that should continue:

```text
{{preset
"instruct": "Speak with a calm, warm audiobook narrator tone.",
"temperature": 0.6,
"top_p": 0.9
}}

This paragraph uses the preset.

This paragraph uses it too.

{{cmd
"instruct": "Say this sentence as a quick excited aside."
}}

Only this sentence overrides the preset.

The next paragraph returns to the preset.

{{reset.preset}}
Back to normal engine settings.
```

Unsupported keys are ignored by engines that do not understand them. Reserved
internal keys such as `engine` and file paths are ignored for safety.

Legacy shorthand still works:

```text
{{cmd "[laugh]"}}
```

This is interpreted as:

```text
{{cmd
"instruct": "[laugh]"
}}
```

Raw bracket text such as `[happy]` written directly in the paragraph is
transparent to LocalText2Voice and may be spoken aloud unless the selected TTS
engine has its own documented bracket syntax.

## Language

Language commands switch the active language for engines that support language
selection per request.

```text
{{lang es}}
{{lang en}}
{{lang.auto}}
```

Accepted values include short codes such as `en`, `es`, `fr`, `de`, `it`, `pt`,
`zh`, `ja`, `ko`, `ru`, and common English/Spanish names such as `Spanish`,
`espanol`, `English`, `French`, `German`, or `Italian`.

Current behavior:

| Engine | Behavior |
| --- | --- |
| Piper | Warning only; use `{{voice "..."}}` to switch to another Piper language |
| Kokoro | Maps language to Kokoro language codes such as `es`, `en-us`, `fr-fr` |
| Chatterbox | Sends the language code to the multilingual model |
| Qwen3 TTS | Maps language to Qwen names such as `Spanish`, `English`, `Italian` |

Example:

```text
{{voice "Abigail"}}
{{lang en}}
Welcome to this short audio lesson.

{{voice "Adrian"}}
{{lang es}}
Ahora cambiamos al espanol con otra voz.
```

## Pronunciation Aliases

Aliases replace later text before it is sent to the TTS engine.

```text
{{alias "GPT" "gee pee tee"}}
{{alias "API" "ay pee eye"}}
```

Example:

```text
{{alias "GPT" "gee pee tee"}}
GPT can help transform a course into audio.
```

The TTS receives:

```text
gee pee tee can help transform a course into audio.
```

## Music And Sound Effects

Music and SFX commands are parsed as postproduction events. They are not sent to TTS.

```text
{{sfx "door.wav"}}
{{music "background.mp3"}}
{{music.stop}}
{{music.volume -20}}
```

Current first implementation records these commands and logs that they are reserved for postproduction. Full timeline mixing from markup will come later.

## Chapters And Marks

Chapters split the internal narration into named groups.

```text
{{chapter "Chapter 1"}}
{{mark "scene_1"}}
```

Example:

```text
{{chapter "Chapter 1"}}
The beginning.

{{chapter "Chapter 2"}}
The next part.
```

Marks are parsed as metadata events for future editing and navigation features.

## Reset

Reset returns narration state to defaults.

```text
{{reset}}
{{reset.voice}}
{{reset.audio}}
```

Defaults:

| State | Default |
| --- | --- |
| Voice | `default` |
| Speed | `1.0` |
| Volume | `0 dB` |
| Language | `auto` |

## Complete Example

```text
{{chapter "Chapter 1"}}
{{voice.narrator}}
{{music "dark_ambient.mp3"}}
{{music.volume -24}}

The house had been abandoned for years.

{{pause.long}}
{{voice.character "Lucia"}}
I do not like this place.

{{voice.character "Pedro"}}
It will only take a minute.

{{sfx "door_creak.wav"}}
{{pause 800}}

{{voice.narrator}}
The door opened slowly.

{{voice.character "Lucia"}}
Did you hear that?

{{cmd "[gasp]"}}

{{voice.narrator}}
No one answered.

{{music.stop}}
{{reset}}
```

## What Is Sent To TTS

Commands such as `pause`, `voice`, `music`, `sfx`, `chapter`, `mark`, and `reset` are not sent as text to the TTS engine.

TTS parameter commands are not sent as transcript text. They are added to the
request configuration for the next segment (`cmd`) or all following segments
(`preset`):

```text
{{preset
"instruct": "Warm narrator tone."
}}

{{cmd
"temperature": 0.8
}}
```

Engines use the keys they support and ignore the rest.

## Current Implementation Status

Implemented now:

- Parser for the main `{{...}}` command syntax.
- Internal event model.
- Warnings for unknown or malformed commands.
- Real silence for `pause`.
- Pronunciation aliases.
- Chapter grouping.
- One-shot TTS parameter overrides with `cmd`.
- Persistent TTS parameter presets with `preset`.
- Voice switching for Piper, Kokoro, Chatterbox, Qwen3 TTS, and OmniVoice.
- Language switching for Kokoro, Chatterbox, Qwen3 TTS, and OmniVoice.
- Qwen `Speaker - Language` voice aliases such as `Serena - Spanish`.
- OmniVoice voice-design attributes are controlled values such as `female`,
  `young adult`, `middle-aged`, `high pitch`, `whisper`, or `british accent`;
  arbitrary style words are not valid OmniVoice `instruct` items.
- Case-insensitive command parsing and voice matching.
- Smart quotes and Unicode dash normalization inside commands.
- Safe fallback for unsupported commands.

Planned later:

- UI help panel and quick insert buttons.
- Timeline music and SFX mixing from markup events.
- Visual markup validation before generation.
