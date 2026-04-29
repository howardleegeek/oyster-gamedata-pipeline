# VCR Testing — `ClaudeThinkingProvider` Smoke Test

We use **[vcrpy](https://vcrpy.readthedocs.io/)** to record one real Anthropic
API call against `ClaudeThinkingProvider`, then replay the cassette
deterministically forever after. This catches regressions in the *wire
contract* (response shape, content-block ordering, thinking-block
preservation) that the pure-mock unit tests cannot see — without spending
money on every CI run.

## Files

- `tests/test_claude_thinking_provider_vcr.py` — the test driver.
- `tests/cassettes/claude_thinking_smoke.yaml` — the recorded (or
  hand-written synthetic) interaction.

## Running locally

```bash
# Replay the cassette (fast, free, deterministic). Default mode.
pytest tests/test_claude_thinking_provider_vcr.py -v

# If vcrpy is not installed, the test skips gracefully:
#   SKIPPED ... vcrpy not installed; pip install vcrpy ...
pip install vcrpy
pytest tests/test_claude_thinking_provider_vcr.py -v
```

## Re-recording when the API contract changes

When Anthropic changes the response shape (e.g. adds a new content-block
type, renames a field, changes a usage counter), the cassette will drift
from reality. To refresh it:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."         # real, paid key
export OYSTER_RECORD_VCR=1                    # opt into RECORD mode
pytest tests/test_claude_thinking_provider_vcr.py -v
```

This will:

1. Make **one** live POST to `https://api.anthropic.com/v1/messages`.
2. Write the resulting interaction to
   `tests/cassettes/claude_thinking_smoke.yaml`, **overwriting** the existing
   file.
3. Redact the `x-api-key` and `authorization` headers via vcrpy's
   `filter_headers=[("x-api-key", "REDACTED"), ("authorization", "REDACTED")]`
   before writing — so the committed file never contains a live secret.

After re-recording, **diff the cassette** to see what shifted:

```bash
git diff tests/cassettes/claude_thinking_smoke.yaml
```

and double-check no key material leaked (search for `sk-ant-`):

```bash
grep -n 'sk-ant-' tests/cassettes/claude_thinking_smoke.yaml || echo "clean"
```

Then commit.

## How the test detects "the contract drifted"

vcrpy is configured with strict request matching:

```python
match_on=("method", "scheme", "host", "path", "query")
```

Since we replay against a fixed cassette under `record_mode="none"`, the
test will **fail loudly** if:

- the request URL changes (e.g. SDK retargets to `/v2/messages`);
- the request method changes (POST → something else);
- the cassette is missing entirely;
- the response no longer round-trips through the SDK's parser into a
  `Message` with a `ThinkingBlock` and a `TextBlock`.

Any of these are real signals that production wiring needs an update.

## Synthetic cassette caveat

If the committed cassette was hand-written (no live API key was available
at authoring time), the file's header comment says so explicitly. The
synthetic cassette still exercises the SDK's response-parsing codepath
end-to-end, but it does **not** validate that Anthropic still emits the
exact byte layout we modeled. Re-record against a live API as soon as
possible to convert the cassette from synthetic to authoritative.

## CI configuration

The test is decorated with `@pytest.mark.integration`. CI should include
this marker in its default selection; if the runner does not have `vcrpy`
installed, the test simply skips — it never blocks the pipeline on a
missing dev dependency.

To enable real replay in CI, add `vcrpy` to the CI image's pip install
list. **Do NOT** export `OYSTER_RECORD_VCR` in CI — recording requires a
paid API key and produces a cassette diff that should be reviewed by a
human, not produced automatically.

## Why not bake `vcrpy` into `pyproject.toml`?

Project policy locks the `[project.optional-dependencies]` block. The
test file gates its `import vcr` with `pytest.importorskip(...)` so that
collection succeeds and the test simply skips when the dependency is
absent. A developer who wants to run the smoke locally installs `vcrpy`
into their venv:

```bash
pip install vcrpy
```
