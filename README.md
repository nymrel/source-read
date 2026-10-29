# Source Read

[![CI](https://github.com/nymrel/source-read/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/nymrel/source-read/actions/workflows/ci.yml)

Source Read is a small, standard-library-only helper that prevents an
unreadable source from being mistaken for a clean result. Version 0.1.0 is a
source-only release under the MIT license; no package-registry publication is
configured. See [RELEASING.md](RELEASING.md).

The helper preserves a distinction that checks often lose:

- a source was consulted successfully and returned an empty value; or
- a source could not be consulted, so a clean verdict would be unsupported.

It records source outcomes supplied by a caller. It does not open files, query
services, authenticate callers, validate source integrity, or enforce a policy.

## Install from the tagged source

Source Read supports Python 3.11 through 3.14. It is not published on PyPI, so
do not use a bare `pip install source-read` command. Install the reviewed
`v0.1.0` source tag instead:

```text
git clone --branch v0.1.0 --depth 1 https://github.com/nymrel/source-read.git
cd source-read
git rev-parse HEAD
# expected: 51ba075b2a956785e45f236db383526979e31922
python -m pip install .
python -I -c "import source_read; print(source_read.__version__)"
# expected: 0.1.0
```

Stop before installation if `git rev-parse HEAD` does not print the expected
commit.

The installed package has no third-party runtime dependencies. The source
installation uses the pinned Hatchling build backend declared in
`pyproject.toml`, so pip may download that build dependency in an isolated
build environment.

## API

```python
from source_read import EXIT_UNKNOWN, UNKNOWN, SourceRead, assess_sources

readable_empty = SourceRead.ok([], label="synthetic inventory")
assert assess_sources([readable_empty], "CLEAR").effective_verdict == "CLEAR"

unreadable = SourceRead.unreadable(
    "synthetic inventory", "synthetic source unavailable", empty=[]
)
assessment = assess_sources([unreadable], "CLEAR")
assert assessment.effective_verdict == UNKNOWN

if assessment.exit_code == EXIT_UNKNOWN:
    raise SystemExit(EXIT_UNKNOWN)
```

`assess_sources()` materializes its iterable exactly once. The returned
assessment keeps an immutable tuple of reads, stable problem summaries, the
requested verdict, the effective verdict, and the effective exit code.
Assessment objects can only be produced by `assess_sources()`; callers cannot
directly construct contradictory result records. Caller verdict text is also
hidden from the default assessment representation.

Zero supplied sources produce `UNKNOWN` and exit code `2` by default. A caller
may deliberately pass `allow_empty=True` only when zero sources are a valid,
reviewed policy state. A successfully read source whose **value** is empty is
not the same thing as zero supplied source records and keeps the caller's
verdict.

The caller's requested exit code is limited to `0` (pass) or `1` (failure).
`2` is reserved for the helper's `UNKNOWN` result and cannot be requested for a
known outcome. The verdict text `UNKNOWN` is reserved as well. The effective
unknown verdict is always that literal value.

Default representations and problem summaries hide values, labels, and failure
reasons. `detailed_problems()` is an explicit disclosure operation; callers
must sanitize its output before logging or sharing it.

## Local validation

No third-party runtime or test dependency is required.

```text
python -m unittest discover -s tests -v
python scripts/verify_release.py
python scripts/build_source_archive.py --check-reproducible
python scripts/verify_release.py --archive dist/source-read-0.1.0.zip
```

These commands validate a source archive. They do not publish it or create a
package-registry release.

## Release

The supported source release is [`v0.1.0`](https://github.com/nymrel/source-read/tree/v0.1.0)
at commit
[`51ba075b2a956785e45f236db383526979e31922`](https://github.com/nymrel/source-read/commit/51ba075b2a956785e45f236db383526979e31922).
Its Linux and Windows validation matrix is recorded in the
[`v0.1.0` CI run](https://github.com/nymrel/source-read/actions/runs/40814314253).

The separately reviewed deterministic validation archive has SHA-256
`51b01355e544ebcad7dd9ef933c6c45924987215cc83a755c0ca10e433556e5b`.
That digest does not describe GitHub's automatically generated ZIP or tarball.
See the [merged release review](https://github.com/nymrel/source-read/pull/1)
for the release evidence and security summary.

## Scope

The package is intentionally small and generic. It does not include internal
incidents, private paths, customer data, operational tooling, or infrastructure
configuration. Its behavior is covered with synthetic examples only.

## License and support

Source Read is licensed under the [MIT License](LICENSE). Maintainer and
security contacts are documented in [MAINTAINERS.md](MAINTAINERS.md) and
[SECURITY.md](SECURITY.md). Contributions are covered by
[CONTRIBUTING.md](CONTRIBUTING.md), ordinary defects can be filed in the
[issue tracker](https://github.com/nymrel/source-read/issues), and sensitive
reports belong in a
[private vulnerability report](https://github.com/nymrel/source-read/security/advisories/new).
