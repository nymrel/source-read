# Source Read

[![CI](https://github.com/nymrel/source-read/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/nymrel/source-read/actions/workflows/ci.yml)

Source Read is a small, standard-library-only helper that prevents an
unreadable source from being mistaken for a clean result. Version 0.2.0 is a
source-only release under the MIT license; no package-registry publication is
configured. See [RELEASING.md](RELEASING.md).

The helper preserves a distinction that checks often lose:

- a source was consulted successfully and returned an empty value; or
- a source could not be consulted, so a clean verdict would be unsupported.

It records source outcomes supplied by a caller. It does not open files, query
services, authenticate callers, validate source integrity, or enforce a policy.

## Install from the tagged source

Source Read requires Python 3.11 or newer. CI tests Python 3.11 through 3.14 on
Linux, Python 3.11 and 3.14 on Windows, and Python 3.14 on macOS. Newer Python
versions are eligible under the package metadata but are not yet validated.

Source Read is not published on PyPI, so do not use a bare
`pip install source-read` command. Install the reviewed source tag instead:

```text
git clone --branch v0.2.0 --depth 1 https://github.com/nymrel/source-read.git
cd source-read
git describe --tags --exact-match
# expected: v0.2.0
python -m venv ../source-read-runtime
# Windows:
../source-read-runtime/Scripts/python -m pip install .
../source-read-runtime/Scripts/python -I -c "import source_read; print(source_read.__version__)"
# POSIX:
../source-read-runtime/bin/python -m pip install .
../source-read-runtime/bin/python -I -c "import source_read; print(source_read.__version__)"
# expected: 0.2.0
```

The sibling virtual environment is intentional. A checkout-local `.venv`
would be an extra file tree inside the exact source-release verification
boundary. The installed package has no third-party runtime dependencies.

An ordinary source installation lets pip create an isolated build environment.
For the reviewed build path, run `python scripts/verify_package_build.py`. It
downloads only the SHA-256-locked wheels in `requirements/build.lock`, installs
them into two disposable build environments, builds twice with index access
disabled, compares the wheel bytes within that interpreter, and performs an
isolated runtime install. Generated wheels are not release artifacts; ZIP
compression can produce different wheel bytes across Python or zlib versions.
The initial Python interpreter, its bundled pip bootstrap, the package index,
and the hosted runner remain trust inputs; hash locking does not make upstream
code safe or provide operating-system network isolation.

## Recommended API

Use `assess_check()` when the caller has a boolean policy result. The required
`passed` argument couples display text to the machine exit code, while source
read failures still force `UNKNOWN` / exit `2`:

```python
from source_read import SourceRead, assess_check

read = SourceRead.ok([], label="synthetic inventory")
assessment = assess_check([read], passed=True)

assert assessment.effective_verdict == "PASS"
assert assessment.is_pass
raise SystemExit(assessment.exit_code)  # exits 0
```

An unreadable source deliberately exits `2` even when the caller computed a
known pass or failure:

```python
from source_read import EXIT_UNKNOWN, SourceRead, assess_check

read = SourceRead.unreadable(
    "synthetic inventory", "synthetic source unavailable", empty=[]
)
assessment = assess_check([read], passed=False)

assert assessment.exit_code == EXIT_UNKNOWN
raise SystemExit(assessment.exit_code)  # deliberately exits 2
```

The library returns an exit code; it never terminates the process itself.

## Lower-level API

`assess_sources()` preserves arbitrary caller verdict text and a separately
supplied exit code. It never guesses that text such as `FAIL` means exit `1`.
Its `verdict_exit_code` defaults to `EXIT_PASS` (`0`), so pass an explicit code
for every known failure:

```python
from source_read import EXIT_FAIL, SourceRead, assess_sources

read = SourceRead.ok(["synthetic finding"], label="synthetic inventory")
assessment = assess_sources(
    [read],
    "POLICY_FAILED",
    verdict_exit_code=EXIT_FAIL,
)

assert assessment.effective_verdict == "POLICY_FAILED"
assert assessment.is_fail
raise SystemExit(assessment.exit_code)  # exits 1
```

| Source state | Requested known outcome | Effective outcome |
| --- | --- | --- |
| all readable | pass / `0` | caller verdict / `0` |
| all readable | failure / `1` | caller verdict / `1` |
| any unreadable | either | `UNKNOWN` / `2` |
| zero sources | either | `UNKNOWN` / `2` by default |

### Compact reference

- `SourceRead.ok(value, label=None)` records a successful read. An empty value
  is still readable. Labels accept strings, pure paths, or `None`.
- `SourceRead.unreadable(label, reason, *, empty=None)` records a failed read.
  `empty` is only a neutral value for continued computation.
- `SourceRead.describe(include_details=False)` returns a generic safe summary.
  `include_details=True` explicitly reveals escaped label and reason text.
- `assess_check(reads, *, passed, verdict=None, allow_empty=False)` is the
  recommended boolean-check API. `passed` must be a real `bool`.
- `assess_sources(reads, verdict, *, verdict_exit_code=EXIT_PASS,
  allow_empty=False)` is the lower-level text/code API.
- `SourceAssessment` exposes immutable source membership, generic `problems`,
  unreadable indexes, requested/effective verdicts and codes, empty-input
  state, `unknown_applied`, and `is_pass` / `is_fail` / `is_unknown`.
- `detailed_problems()` explicitly reveals escaped labels and reasons.
- `EXIT_PASS`, `EXIT_FAIL`, and `EXIT_UNKNOWN` are `0`, `1`, and `2`;
  `UNKNOWN` is the reserved unknown-verdict text.

`assess_sources()` and `assess_check()` materialize their iterable exactly
once. Zero supplied sources produce `UNKNOWN` and exit `2` by default. A caller
may deliberately pass `allow_empty=True` only when zero sources are a valid,
reviewed policy state. A successfully read source whose value is empty is not
the same thing as zero supplied source records.

`problems` describes source-read problems, not the policy result. A known
failure can have `problems == ()`. Records and assessments also have ordinary
Python object truthiness; do not use `if assessment`, `if read`, or an empty
problems tuple as a success test. Inspect `exit_code`, the `is_*` properties,
`unknown_applied`, or `readable` as appropriate.

Assessment records are frozen, but caller-owned payload values are not deeply
copied or frozen. Default representations and generic problem summaries hide
values, labels, reasons, and caller verdict text. `describe(include_details=True)`
and `detailed_problems()` are explicit disclosure operations; sanitize their
output before logging or sharing it.

## Local validation

The runtime and unit-test suite use only the Python standard library. The
locked package-build validation downloads only reviewed, hashed build wheels.

```text
python -m unittest discover -s tests -v
python scripts/verify_release.py
python scripts/build_source_archive.py --check-reproducible
python scripts/verify_release.py --archive dist/source-read-0.2.0.zip
python scripts/verify_package_build.py
```

These commands validate source and packaging artifacts. They do not publish to
a registry or create a hosted release.

## Releases

The current supported source release is
[`v0.2.0`](https://github.com/nymrel/source-read/releases/tag/v0.2.0). Its
release page records the exact tag target, source-archive digest, independent
review, hosted checks, and GitHub asset attestation. Tagged documentation is
immutable release-time evidence; the [current main documentation](https://github.com/nymrel/source-read/blob/main/README.md)
may contain later guidance.

The historical initial release remains available as immutable evidence:
[`v0.1.0`](https://github.com/nymrel/source-read/releases/tag/v0.1.0), tag
target
[`51ba075b2a956785e45f236db383526979e31922`](https://github.com/nymrel/source-read/commit/51ba075b2a956785e45f236db383526979e31922),
and [release review PR #1](https://github.com/nymrel/source-read/pull/1).
Never move an existing release tag to pick up documentation changes.

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
