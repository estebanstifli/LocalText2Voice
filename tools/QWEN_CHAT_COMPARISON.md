# Qwen chat comparison — 2026-09-09

Nine isolated requests against local Ollama qwen3:8b (8.2B Q4_K_M).
Production code and the open project were not changed.
Free-text cases use the identical complete 4681-character source recovered from
the project's earlier log. Structured cases replay appearance request 12 from
analysis-requests-20260908-175336-743319.txt. Seed 42 was added to all experiments;
one sample per case, so these observations are not a general benchmark.

The app hardcodes think=false for both free and structured requests. Free text
uses temperature 0.2, repetition penalty 1.08 and window 1024. Structured first
attempt uses 0.15, 1.12 and 2048. The local model's stored defaults are temperature
0.6, top_p 0.95, top_k 20 and repetition penalty 1. The experiments that use model
defaults still explicitly set context/output budgets and the shared seed.

## Observations

| Case | Seconds | Result |
| --- | ---: | --- |
| App prompt/options, no thinking, whole story | 5.03 | Correct eight individuals; no stable appearance invented. Includes 3.55 s model loading. |
| Same, thinking enabled | 10.48 | Eight individuals; adds wolf's temporary red face. |
| App prompt, default sampling, thinking | 5.36 | Eight individuals; mixes houses into appearance. |
| Simple Spanish question, app options, no thinking | 8.19 | Includes secondary characters but mistranslates woodcutter as talabartero. |
| Simple question, default sampling, thinking | 12.12 | Includes secondary characters but calls woodcutter talañero. |
| Simple question, default sampling, no thinking | 4.32 | Again mistranslates woodcutter as talabartero. |
| Structured appearance, app options, no thinking | 3.63 | Creates profiles but adds relative age, mood and carried straw. |
| Same structured request, thinking | 7.72 | Differentiates pigs; introduces props linked to story materials. |
| Structured request, default sampling, thinking | 13.78 | Pig profiles become straw/stick/brick accessories rather than clean stable portraits. |

All responses ended with stop. Two free-text cases encountered only a Windows
console emoji-printing error AFTER saving complete answers/raw streams; these are
not Ollama/API failures. The script now escapes unsupported console characters.

The structured prompt explicitly authorizes inventing compatible missing visual
details. Invention there is not automatically an error; the problem is profile
quality, unnecessary props, and unsupported age relationships.

Conclusion: there is a real configuration difference from normal chat, but no
evidence of broken HTTP transport. Thinking alone is not a complete fix. The
current first prompt already works on the whole story; further controlled testing
should target profile generation, binding and block context, independently.

Artifacts:
- tmp/qwen-chat-comparison/20260909-174832-913760 (six free-text cases)
- tmp/qwen-chat-comparison/20260909-174948-969147 (three structured cases)

Official thinking behavior: https://docs.ollama.com/capabilities/thinking
