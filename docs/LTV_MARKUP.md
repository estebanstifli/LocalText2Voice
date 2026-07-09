# LocalText2Voice Markup

LocalText2Voice Markup, or LTV Markup, lets you add narration instructions inside your source text using double braces.

Example:

```text
The house had been empty for years.
{{pause.long}}
{{emotion whisper}}
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

Voice matching is also case-insensitive when LocalText2Voice can map the name to
an installed voice for the selected engine.

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

| Command | Piper | Kokoro | Chatterbox | Qwen3 TTS | OpenAI | ElevenLabs | Gemini | Azure | Notes |
| --- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | --- |
| `{{pause ...}}` | * | * | * | * | * | * | * | * | App inserts real silence between segments. |
| `{{chapter "..."}}` | * | * | * | * | * | * | * | * | App creates named internal groups before generation. |
| `{{alias "A" "B"}}` | * | * | * | * | * | * | * | * | App replaces later text before TTS. |
| `{{reset}}` | * | * | * | * | * | * | * | * | App resets active markup state. |
| `{{voice "..."}}` | * | * | * | * |  |  |  |  | Switches installed/local voices or Qwen speaker/language aliases. |
| `{{lang ...}}` |  | * | * | * |  |  |  |  | Piper language is tied to the selected voice model. |
| `{{speed ...}}` | * | * |  |  | * |  |  | * | Only engines with connected speed/rate parameters are marked. |
| `{{emotion ...}}` |  |  |  | * |  |  |  |  | Qwen maps emotion to natural-language `instruct`. |
| `{{cmd "..."}}` |  |  | * | * |  |  |  |  | Chatterbox receives compatible tags as text; Qwen receives them as `instruct`. |
| `{{sendcommand "..."}}` |  |  | * | * |  |  |  |  | Alias of `cmd`. |
| `{{sendcomand "..."}}` |  |  | * | * |  |  |  |  | Tolerant misspelling alias of `sendcommand`. |
| `{{volume ...}}` |  |  |  |  |  |  |  |  | Parsed for future postproduction; not active yet. |
| `{{music "..."}}` |  |  |  |  |  |  |  |  | Parsed/reserved for future markup timeline mixing. |
| `{{music.stop}}` |  |  |  |  |  |  |  |  | Parsed/reserved for future markup timeline mixing. |
| `{{music.volume ...}}` |  |  |  |  |  |  |  |  | Parsed/reserved for future markup timeline mixing. |
| `{{sfx "..."}}` |  |  |  |  |  |  |  |  | Parsed/reserved for future sound effects. |
| `{{mark "..."}}` |  |  |  |  |  |  |  |  | Parsed/reserved for future editing/navigation markers. |

Recommended mental model:

- App-level commands shape the audiobook timeline and are safe with every TTS engine.
- Voice/language commands are chunk boundaries; LocalText2Voice will not mix two voices or languages in one request.
- Qwen style control should use `{{emotion ...}}` or `{{cmd "natural language instruction"}}`, not raw `[tags]` in the text.

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

If a voice is not found, LocalText2Voice writes a warning and keeps using the
voice selected in the UI.

Qwen note: the UI displays combinations such as `Serena - Spanish`, but Qwen
internally receives two separate values: `speaker=Serena` and
`language=Spanish`. These combinations are accepted in markup:

```text
{{voice "Serena - Spanish"}}
{{voice "Serena – Spanish"}}
```

## Speed

Speed commands are parsed as active narration state.

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

Volume commands are parsed for future postproduction support.

```text
{{volume -3}}
{{volume.normal}}
```

Values are in dB.

## Emotion

Emotion commands are parsed as active narration state.

```text
{{emotion happy}}
{{emotion sad}}
{{emotion angry}}
{{emotion scared}}
{{emotion whisper}}
{{emotion.neutral}}
```

Current behavior depends on the TTS engine:

| Engine | Current behavior |
| --- | --- |
| Piper | Ignored with warning |
| Kokoro | Parsed, reserved for supported backends |
| Chatterbox | Parsed, reserved for future prompt/tag support |
| Qwen | Converted into a natural-language `instruct` value for the current chunk |

Example:

```text
{{emotion whisper}}
Do not make a sound.

{{emotion.neutral}}
The narrator continues normally.
```

## Direct Model Commands

Direct model commands are only sent to engines that can use them safely.

```text
{{cmd "[laugh]"}}
{{sendcommand "[gasp]"}}
{{sendcomand "[sigh]"}}
```

`sendcomand` is accepted as a tolerant alias of `sendcommand`.

Current behavior:

| Engine | Behavior |
| --- | --- |
| Piper | Ignored with warning |
| Kokoro | Ignored with warning |
| Chatterbox | Inserted into the text sent to the model |
| Qwen | Converted into a natural-language `instruct` value for the current chunk |

Example:

```text
This is funny.
{{cmd "[laugh]"}}
But nobody laughed for long.
```

For Qwen, prefer natural-language instructions because the model's documented
style control uses the `instruct` parameter:

```text
{{cmd "Speak with a warm, encouraging teacher tone."}}
Now repeat the phrase slowly.
```

Raw bracket text such as `[happy]` written directly in the paragraph is treated
as normal transcript text and may be spoken aloud.

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
{{reset.emotion}}
```

Defaults:

| State | Default |
| --- | --- |
| Voice | `default` |
| Speed | `1.0` |
| Volume | `0 dB` |
| Emotion | `neutral` |
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
{{emotion whisper}}
I do not like this place.

{{voice.character "Pedro"}}
{{emotion.neutral}}
It will only take a minute.

{{sfx "door_creak.wav"}}
{{pause 800}}

{{voice.narrator}}
The door opened slowly.

{{voice.character "Lucia"}}
{{emotion scared}}
Did you hear that?

{{cmd "[gasp]"}}

{{voice.narrator}}
No one answered.

{{music.stop}}
{{reset}}
```

## What Is Sent To TTS

Commands such as `pause`, `voice`, `emotion`, `music`, `sfx`, `chapter`, `mark`, and `reset` are not sent as text to the TTS engine.

Only direct model commands may be sent, and only for compatible engines:

```text
{{cmd "[laugh]"}}
```

For Piper and Kokoro, direct model commands are ignored with a warning. For
Qwen, direct model commands are converted into the request's `instruct`
parameter instead of being sent as transcript text.

## Current Implementation Status

Implemented now:

- Parser for the main `{{...}}` command syntax.
- Internal event model.
- Warnings for unknown or malformed commands.
- Real silence for `pause`.
- Pronunciation aliases.
- Chapter grouping.
- Direct model commands for compatible engines.
- Voice switching for Piper, Kokoro, Chatterbox, and Qwen3 TTS.
- Language switching for Kokoro, Chatterbox, and Qwen3 TTS.
- Qwen `Speaker - Language` voice aliases such as `Serena - Spanish`.
- Qwen emotion and direct command mapping to `instruct`.
- Case-insensitive command parsing and voice matching.
- Smart quotes and Unicode dash normalization inside commands.
- Safe fallback for unsupported commands.

Planned later:

- UI help panel and quick insert buttons.
- Emotion mapping per engine.
- Timeline music and SFX mixing from markup events.
- Visual markup validation before generation.
