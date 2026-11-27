"""Keep task fixtures and hidden tests out of the repository test collection."""

collect_ignore_glob = [
    "*/fixture/tests",
    "*/fixture/tests/*",
    "*/hidden_tests",
    "*/hidden_tests/*",
]
