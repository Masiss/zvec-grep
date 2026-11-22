# Owner approval — R4 real-agent evaluation

**No private-source inference has been authorized or executed.** Please fill/approve these decisions before any real API call.

## Proposed run

- **Scope:** frozen D01–D12 only; first a pre-registered D01, D06, D12 pilot across Arms A/B/C (9 answer sessions), then the full 12×3 only if the pilot passes. H01–H08 stay sealed.
- **Answerer/planner provider and endpoint:** OpenAI Responses API, https://api.openai.com/v1/responses.
- **Proposed answerer identity:** pin the driver’s current GPT-5.5 default to official snapshot gpt-5.5-2026-04-23; record provider-returned model identity and system fingerprint for every call. Owner approval required.
- **Evaluator identity (D1):** owner to name a separate, trusted evaluator and approve its instructions and packet fields. The previous gpt-4.1 candidate is marked deprecated; it will not be used. Prompt digest: 4e9e20413c8ae2172844298ed743ab24c5fb79c0a36defeb957bee0c4f28cd55.
- **Private data that can leave:** the answerer gets each frozen question, tool schema and tool result; tool results can carry private source excerpts within the frozen pack/read limits. Arm-B planning gets the question and folder hints only. The evaluator gets the question, answer/claims/citations, delivered pack metadata/content and cited spans. It never gets gold spans, C3 checklist, answerer transcript/session or corpus outside delivered/cited evidence.
- **Retention:** the driver currently omits store:false. OpenAI documents 30-day Responses application state by default and up to 30-day abuse logs unless the account is eligible for and configured with approved retention controls. Specify the actual project setting; no ZDR/MAM assumption is made.
- **Maximum cost:** currently **unbounded by the harness** because it sets no output-token ceiling and has no aggregate reservation guard. The evaluator-only historical volume estimate re-priced at GPT-5.5 standard rates is about $2.42, excluding answerer/planner calls and usage variance. Required owner decision: approve a total USD ceiling; before inference, the harness must enforce request/input bounds, max_output_tokens, no automatic retries, and a pre-call reserve against that ceiling. A billing alert is not a hard cap.
- **Runtime:** reuse the existing frozen v3/Potion Code 16M CPU index. No rebuild, repair, model download/change, or production default/API change is included. Stop if execution needs a persistent runtime/index mutation.

## Approval record

- D1 evaluator identity and prompt: **pending**
- D2 private-source inference to the named provider/model/endpoint and above data flow: **pending**
- Retention mode/account setting: **pending**
- Maximum all-in spend (USD): **pending**
- Served-identity mismatch rule: **pending** (recommended: abort the arm/pilot if it differs from the pinned identity)
- Pilot approval after these gates: **pending**

References checked 2026-11-22: [GPT-5.5 snapshot and pricing](https://developers.openai.com/api/docs/models/gpt-5.5), [API pricing/model status](https://developers.openai.com/api/docs/pricing), [API data controls](https://developers.openai.com/api/docs/guides/your-data).
