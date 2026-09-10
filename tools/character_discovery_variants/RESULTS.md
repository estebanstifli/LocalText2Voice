# Second prompt experiment — 2026-09-08

Seven isolated calls to local Ollama `qwen3:8b`, using the identical 4681-character
Three Little Pigs narration. No later analysis stages or project writes.
All calls: think=false, temperature=0.2, num_ctx=8192, num_predict=8096,
repeat_penalty=1.08, repeat_last_n=1024. No fixed seed; one sample per prompt.
All seven ended with done_reason=stop. This is manual assessment of this story,
not a general-purpose benchmark or proof of repeatability.

Expected individuals: mother, first/second/third little pig, farmer, woodcutter,
bricklayer, wolf. No detailed stable appearance or lasting physical change is
specified. The wolf's red face during exertion is not a lasting change. The
chinny-chin-chin dialogue is not sufficient to design detailed facial hair.

| Variant | Result |
| --- | --- |
| 08 Identity first | All eight; still describes actions, houses and carried materials. |
| 09 Evidence first | Clean eight-character list; appearance unspecified for each. Candidate. |
| 10 Casting notes | Invents clothing, colors and features; adds objects and places. Reject. |
| 11 Two sections | Clean eight-character inventory and separate statement that physical descriptions/lasting changes are absent. Preferred candidate for further tests. |
| 12 Literal editor | All eight; replaces physical description with actions and possessions. Reject. |
| 13 Minimal | Adds houses, chimney, pot, fireplace, bush, wind, trees and dinner. Reject. |
| 14 Spanish, original names | All eight; invents human hair, eyes, skin and clothing for pigs. Reject. |

Outputs are under `tmp/character-discovery-round2/<variant>/<timestamp>/`:
`request.json`, `narration.txt`, `prompt.txt`, `response.txt`, `raw-response.jsonl`.
The application default was not changed by this round. Before choosing a default,
repeat candidates and test passages with explicit appearance, physical evolution,
aliases and non-human characters. Do not advance to structured analysis yet.
