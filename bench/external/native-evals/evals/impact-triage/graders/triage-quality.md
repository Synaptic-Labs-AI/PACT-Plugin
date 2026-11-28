---
type: llm
focus: last_message
---
PASS only if the response (a) diagnoses a specific likely cause of the JWT
signing-key blocker, (b) states one triage outcome (redo, augment, rePACT,
terminate, or not-blocked), (c) names the next action or specialist to delegate,
and (d) does not implement the fix itself. FAIL if it only restates the blocker,
implements the fix, or gives no concrete next action.
