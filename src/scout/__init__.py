"""Scout: GitHub repository agent — triage, feedback sync, and setup.

Distributable GitHub Actions live in this package; each has a thin composite
wrapper (action.yml at the repo root for triage, actions/<name>/ for the rest)
that installs this package and invokes the matching console-script entry point.
"""
