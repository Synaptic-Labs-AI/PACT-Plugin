"""
The backlog's github.com slug, end to end, against fixed expected values.

`shared.backlog._slug_from_remote_url` (the tracker-ref resolver) reads a
remote through ONE parser, the memory canonicaliser
`project_identity.normalize_remote_url`, and takes `(owner, repo)` from a
`github.com/<owner>/<repo>` id whose names are safe slug characters
(`_valid_slug`). There is no second parser to agree with, so these tests pin
the composite's output for a representative corpus, including the inputs
that must be refused for a security reason:

- any backslash, and
- more than one `@`.

`urlsplit` and git disagree about where the host ends in
`https://evil.com\\@github.com/o/n`: it is github.com to one reading and not
the other. Accepting it could make the slug name a repository the remote
does not talk to.

RED WHEN a spelling's slug changes: a refusal is lost, a served spelling
stops resolving, or the slug check (`_valid_slug`) or the id stops refusing
a form it refuses.
"""

import pytest

from shared.backlog import _slug_from_remote_url

# The inputs refused for a security reason, by the slug here and by the
# memory id (`test_remediation_identity_parser` checks the id on them too).
SHARED_REFUSALS = (
    ("https://evil.com\\@github.com/o/n", "backslash + @ ambiguity (the measured case)"),
    ("git@host:owner\\repo", "a backslash in the path"),
    ("https://a@b@host/owner/repo", "more than one @"),
    ("git@a@b:owner/repo", "more than one @ in scp form"),
    ("https://a@b@github.com/o/n", "more than one @, github.com host"),
    ("git@a@b@github.com:o/n", "more than one @ in scp form, github.com host"),
)


def test_every_security_refusal_gives_no_slug():
    for url, rule in SHARED_REFUSALS:
        assert _slug_from_remote_url(url) is None, (url, rule)


# (url, the slug the backlog must read, or None), fixed by hand.
CORPUS = [
    # Served github.com spellings: one repository, one lower-cased slug.
    ("https://github.com/Owner/Repo.git", ("owner", "repo")),
    ("https://github.com/o/r", ("o", "r")),
    ("https://github.com/o/r/", ("o", "r")),
    ("https://github.com/o/r.git/", ("o", "r")),
    ("https://github.com/O/R.GIT", ("o", "r")),
    ("https://user:tok@GitHub.com/o/r.git", ("o", "r")),
    ("http://github.com/o/r", ("o", "r")),
    ("https://github.com:443/o/r", ("o", "r")),
    ("https://www.github.com/O/R", ("o", "r")),
    ("ssh://git@github.com/o/r.git", ("o", "r")),
    ("ssh://git@github.com:22/o/r", ("o", "r")),
    ("ssh://git@ssh.github.com:443/o/r.git", ("o", "r")),
    ("git+ssh://git@github.com/o/r", ("o", "r")),
    ("ssh+git://git@github.com/o/r.git", ("o", "r")),
    ("git://github.com/o/r", ("o", "r")),
    ("git@github.com:o/r.git", ("o", "r")),
    ("me@github.com:o/r", ("o", "r")),
    ("github.com:o/r", ("o", "r")),
    ("git@ssh.github.com:o/r", ("o", "r")),
    ("https://github.com/o/r\n", ("o", "r")),
    ("https://github.com/a.b/c_d-e", ("a.b", "c_d-e")),
    # Refused for a security reason: backslash, more than one `@`.
    ("https://evil.com\\@github.com/o/n", None),
    ("git@github.com:owner\\repo", None),
    ("https://a@b@github.com/o/n", None),
    ("git@a@b@github.com:o/n", None),
    # Names that are not slugs: all-dots, a quote, a space, a home path.
    ("https://github.com/o/...", None),
    ("https://github.com/o/..", None),
    ("https://github.com/./r", None),
    ('https://github.com/o/r"x', None),
    ("https://github.com/o/r x", None),
    # Spellings the id declines or names no github.com repository with.
    ("https://github.com/o", None),
    ("https://github.com/o/r/extra", None),
    ("https://github.com/O/R%2Fx", None),
    ("https://github.com/o~x/r", None),
    ("https://github.com/git/git%2Egit", None),
    ("https://github.com/git/git%2Egit.git", ("git", "git.git")),
    ("https://github.com/o/%41b", ("o", "ab")),
    ("https://github.com/o/r/.git", None),
    ("git@github.com:~/o/r", None),
    ("ssh://git@github.com/~/o/r", None),
    ("https://github.com/o/r?x", None),
    ("https://github.com/o/r#x", None),
    ("https://github.com:8443/o/r", None),
    ("ssh://git@github.com:022/o/r", None),
    ("https://github.com:00443/o/r", None),
    ("https://ssh.github.com/o/r", None),
    ("ssh://git@ssh.github.com:2222/o/r", None),
    ("https://www.github.com:8443/o/r", None),
    ("  https://github.com/o/r\n", None),
    ("https://gitlab.com/o/r", None),
    ("https://github.example.com/o/r", None),
    ("ftp://github.com/o/r", None),
    ("codecommit::us-east-1://r", None),
    (None, None),
    ("", None),
    ("not a url", None),
]


@pytest.mark.parametrize("url, expected", CORPUS)
def test_the_backlog_slug_of_each_spelling(url, expected):
    assert _slug_from_remote_url(url) == expected


def test_the_corpus_holds_both_outcomes():
    """Control: the corpus pins served spellings and refusals alike, so a
    parser that answered None (or a slug) for everything would red it."""
    served = [url for url, expected in CORPUS if expected is not None]
    refused = [url for url, expected in CORPUS if expected is None]
    assert len(served) >= 20 and len(refused) >= 25, (len(served), len(refused))
