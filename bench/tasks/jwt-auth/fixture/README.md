# authsvc

Tiny in-memory authentication service used by the platform team as a practice
codebase. It has no network layer: requests are plain dictionaries passed to
`Api.handle_request` and responses are plain dataclasses, so the service can be
driven entirely from tests.

## Layout

- `authsvc/users.py` - process-local user store (`UserStore`, `User`) with
  salted PBKDF2-HMAC-SHA256 password hashes.
- `authsvc/api.py` - request router (`Api`, `Response`).
- `tests/` - pytest suite for the current behavior.

## Running the tests

    python -m pytest -q

Python 3.12, standard library only. `pytest` is available in the project
environment.
