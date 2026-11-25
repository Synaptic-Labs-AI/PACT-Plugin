---
type: llm
focus: last_message
---
PASS only if the response reports a stored PACT memory entry with a concrete
entry id, shows retrieval of the stored convention, and reports no storage error.
FAIL if it only says the convention was saved, echoes the convention without an
id or retrieval, or reports a failed CLI/store call.
