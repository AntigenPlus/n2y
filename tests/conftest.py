# Tests for the unsupported plugin tier (see README, "Unsupported Plugins")
# are not part of the quality gate. This is anchored to this directory, so
# it holds no matter where pytest is invoked from; an explicit
# `pytest tests/unsupported` still runs them.
collect_ignore = ["unsupported"]
