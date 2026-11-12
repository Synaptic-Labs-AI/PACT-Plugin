"""
Cross-parser agreement: two remote-URL parsers, one security rule.

`project_identity.normalize_remote_url` (the memory canonicaliser) and
`shared.backlog._slug_from_remote_url` (the tracker-ref resolver) parse the
same inputs for different purposes. They deliberately diverge on the HOST
filter — the backlog one is github.com-only — but they must never diverge on
the two REFUSALS that exist for a security reason:

- any backslash, and
- more than one `@`.

`urlsplit` and git disagree about where the host ends in
`https://evil.com\\@github.com/o/n`: it is github.com to one reading and not
the other. A parser that accepts it can be made to name a repository it is not
talking to. Two parsers in one repo with different rules on that input is the
defect; this module is what keeps them aligned.

RED WHEN: either parser loses a refusal. It does NOT assert that the two agree
on accepted forms — the host-filter divergence is intentional.
"""

from scripts.project_identity import normalize_remote_url
from shared.backlog import _slug_from_remote_url

# Inputs that BOTH parsers must refuse. Each names the rule it exercises.
SHARED_REFUSALS = (
    ("https://evil.com\\@github.com/o/n", "backslash + @ ambiguity (the measured case)"),
    ("git@host:owner\\repo", "a backslash in the path"),
    ("https://a@b@host/owner/repo", "more than one @"),
    ("git@a@b:owner/repo", "more than one @ in scp form"),
    # THE TWO ROWS THE BACKLOG PARSER'S `@` RULE ALONE REFUSES. The two rows
    # above name a non-github host, so the backlog parser's host filter would
    # refuse them even without the `@` rule. These name github.com.
    ("https://a@b@github.com/o/n", "more than one @, github.com host"),
    ("git@a@b@github.com:o/n", "more than one @ in scp form, github.com host"),
)


def test_both_parsers_refuse_the_shared_corpus():
    """Every shared-refusal input is refused by BOTH parsers."""
    for url, rule in SHARED_REFUSALS:
        assert normalize_remote_url(url) is None, (
            f"the memory canonicaliser accepted {url!r} ({rule})"
        )
        assert _slug_from_remote_url(url) is None, (
            f"the backlog parser accepted {url!r} ({rule})"
        )


def test_the_parsers_are_live_on_a_shared_positive_control():
    """A github.com remote both parsers can read, so a null refusal is meaningful.

    Without this arm both refusals could pass because a parser had stopped
    working altogether.
    """
    url = "https://github.com/Owner/Repo.git"
    assert normalize_remote_url(url) == "github.com/Owner/Repo"
    assert _slug_from_remote_url(url) == ("Owner", "Repo")