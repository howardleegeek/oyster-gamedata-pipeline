# D13 — Strict no-placeholder lint
Implement `bin/lint_no_placeholder.py path/`. Greps every .py/.sh/.md under path for the words `placeholder|stub|stop-gap|dummy|testsrc|hardlink` outside test files. Exits 1 if any match. Pure stdlib. Tests: clean tree → 0, dirty tree → 1.
