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

RED WHEN: either parser loses a refusal, or the backlog's slug and the
memory id disagree on any spelling of a generated corpus: the slug is
`(owner, repo)` exactly when the id is `github.com/<owner>/<repo>` with slug
characters, and None otherwise (the host filter is that rule).
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
    assert normalize_remote_url(url) == "github.com/owner/repo"
    assert _slug_from_remote_url(url) == ("owner", "repo")


def _github_remote_corpus():
    """Spellings of github.com remotes and their near misses: every scheme
    and the scp form, with and without a user, each GitHub host, default,
    other and leading-zero ports, mixed case, `.git` forms, `~/`, percent
    escapes, one and three segments, and query or fragment tails."""
    hosts = ["github.com", "GitHub.com", "www.github.com", "ssh.github.com", "gitlab.com"]
    ports = ["", ":22", ":443", ":80", ":9418", ":8443", ":022", ":00443", ":"]
    paths = [
        "Acme/Widget", "acme/widget.git", "Acme/Widget.GIT", "Acme/Widget/", "Acme/Widget/.git",
        "acme/widget.git.git", "~/acme/widget", "acme/wid%67et", "acme", "acme/widget/x",
        "acme/widget?x", "acme/widget#x", "a.b/c_d-e",
    ]
    for scheme in ["https", "http", "ssh", "git", "git+ssh", "ssh+git"]:
        for user in ["", "git@"]:
            for host in hosts:
                for port in ports:
                    for path in paths:
                        yield f"{scheme}://{user}{host}{port}/{path}"
    for user in ["", "git@", "me@"]:
        for host in hosts:
            for path in paths:
                yield f"{user}{host}:{path}"


def _slug_the_id_names(url):
    identifier = normalize_remote_url(url)
    if identifier is None:
        return None
    host, _, path = identifier.partition("/")
    owner, _, name = path.partition("/")
    if host != "github.com" or not name or "/" in name:
        return None
    import re

    allowed = re.compile(r"^[A-Za-z0-9._-]+\Z")
    if not (allowed.match(owner) and allowed.match(name)) or {".", ".."} & {owner, name}:
        return None
    return owner, name


def test_the_backlog_slug_and_the_id_agree_on_every_accepted_form():
    """Accepted-form parity over the generated corpus, not only the shared
    refusals: the backlog names a github.com repository exactly when the
    memory id does, and the same one."""
    corpus = list(_github_remote_corpus())
    accepted = [url for url in corpus if _slug_the_id_names(url) is not None]
    assert len(corpus) > 3000 and len(accepted) > 100, (len(corpus), len(accepted))  # control: the corpus is live
    disagree = [
        (url, _slug_from_remote_url(url), _slug_the_id_names(url))
        for url in corpus
        if _slug_from_remote_url(url) != _slug_the_id_names(url)
    ]
    assert disagree == [], (len(disagree), disagree[:10])
