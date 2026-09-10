# Conversational scene-first trial — 2026-09-09

Continued the three-turn conversation from
`tmp/storyboard-conversation/20260909-181703-131856` with two further calls.
No production pipeline or project data was changed.

## Turn 4: proposed scene starts

Prompt: Where does each visual scene you proposed begin in the original story?
For each scene, including the subscenes, give its title and the exact 2–3 words
that begin its passage. Only the start, not the end. List them in story order.

Response: `tmp/storyboard-conversation/20260909-182951-311520/answer-4.txt`.
30.47 seconds; normal stop. Returned ten entries with mostly 4–8-word quotes.
All ten quotes can be found after case/punctuation normalization. This does NOT
mean their scene meaning or chronological placement is correct.

Cue-start times below come from the prior project log
`analysis-requests-20260908-135127-170798.txt`. Quotes inside a cue cannot yield
an exact word-start time from these cue timestamps alone.

| Entry | Proposed scene | Matching cue start |
| --- | --- | ---: |
| 1 | Departure | 9.03 s |
| 2 | First pig's house | 27.23 s |
| 3 | Second pig's house | 55.63 s |
| 4 | Third pig's house | 73.73 s |
| 5 | First wolf attack | 104.18 s |
| 6 | Second wolf attack | 145.76 s |
| 7 | Third wolf attack | 233.92 s |
| 8 | Roof/chimney plan | 256.97 s |
| 9 | Wolf's defeat | 223.54 s — backwards, wrong event |
| 10 | Happy ending | 245.47 s — premature celebration during conflict |

The departure quote describes the mother speaking, whereas hugging goodbye occurs
later. The proposals omit or subsume some intervening beats. Sorting entries by
time would not repair these semantic errors. The first video image can cover 0 s,
but its narration anchor must be retained separately from that video-start rule.

Two/three words are not reliably unique: `the first little` occurs three times,
`the three little` six, and `the wolf climbed` twice in this narration.

## Turn 5: one long-scene subdivision

Used the first-pig passage (27.23–55.63 s, 28.4 s), asking for three images around
9.5 seconds each and including that passage as context in the same conversation.
Response: `tmp/storyboard-conversation/20260909-183122-320800/answer-5.txt`.
10.7 seconds; normal stop.

Proposed: wide view of farmer and straw; medium close-up of pig building the
yellow house; low-angle view of pig dancing while brothers leave.

Useful visual coverage, but not a pure camera variation: these are successive
actions, so assigning equal durations without checking their own source anchors
can advance construction/dancing before their narration. It also introduces
a camera pan despite asking for one image, a new apparent hat change, and extra
details. Candidate visual drafts still need review.

## Recommendation

Let the LLM propose scenes first. Keep application responsibility for literal
quote matching, ambiguity, chronological validation, audio timing, first-frame
coverage, duration arithmetic and warnings. Request longer unique source quotes
when needed. A source match alone is insufficient; reject or review wrong-event
anchors rather than sorting them silently. Generate coverage only after valid
boundaries; any new narrative action needs its own checked anchor.
