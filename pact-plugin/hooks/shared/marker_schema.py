"""
Location: pact-plugin/hooks/shared/marker_schema.py
Summary: Shared marker schema constants and signature function for the
         bootstrap-complete marker. Imported by both the producer
         (bootstrap_marker_writer.py) and the verifier (bootstrap_gate.py)
         so a single source of truth defines the marker's content
         invariants.
Used by: bootstrap_marker_writer.py (producer), bootstrap_gate.py (verifier),
         tests/test_marker_schema.py, tests/test_bootstrap_gate.py,
         tests/test_bootstrap_marker_writer.py

Coupling: this module is the SSOT for the marker's wire format. Producer
and verifier MUST import the same MARKER_SCHEMA_VERSION,
MARKER_MAX_BYTES, and expected_marker_signature so their digests
align byte-for-byte. Bumping MARKER_SCHEMA_VERSION invalidates
pre-bump markers automatically (via the digest input).

Security note: the signature is NOT cryptographic provenance. All four
inputs to the digest are readable from the same-user filesystem
(session_id + plugin_root from pact-session-context.json,
plugin_version from plugin.json, marker_version from this module).
A same-user attacker with read access can recompute the digest. The
signature is a fingerprint that closes the trivial Bash-touch bypass
(#662) and creates a detection surface for forgery; it is not a MAC.
"""

import hashlib

# Marker schema version. Bump if marker JSON shape changes; verifier
# rejects unknown versions. Producer (bootstrap_marker_writer.py) and
# verifier (bootstrap_gate.py:is_marker_set) both import this.
MARKER_SCHEMA_VERSION = 1

# Marker file size cap (bytes). The marker JSON is a small fixed schema
# ({v, sid, sig}); content larger than this is rejected to defend against
# pathological reads. Producer asserts len(payload) <= MARKER_MAX_BYTES
# before write; verifier rejects any on-disk file larger than this.
MARKER_MAX_BYTES = 256


def expected_marker_signature(session_id: str, plugin_root: str,
                              plugin_version: str, marker_version: int) -> str:
    """Compute the expected SHA256 marker signature.

    Inputs are joined with `|` separators in a fixed order so producer and
    verifier compute the same digest:

        sha256(f"{session_id}|{plugin_root}|{plugin_version}|{marker_version}")

    The `marker_version` is part of the digest so a format-version bump
    invalidates pre-bump markers automatically.
    """
    payload = f"{session_id}|{plugin_root}|{plugin_version}|{marker_version}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
