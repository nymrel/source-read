# Source Read

[![CI](https://github.com/nymrel/source-read/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/nymrel/source-read/actions/workflows/ci.yml)

Source Read is a small, standard-library-only helper that prevents an
unreadable source from being mistaken for a clean result. This source tree
identifies itself as version 0.2.0. Versioned releases are source-only under
the MIT license; no package-registry publication is configured. See
[RELEASING.md](RELEASING.md).

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
`pip install source-read` command. For a published release, install the
reviewed source tag instead:

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
disabled, compares the wheel bytes within that interpreter, performs an
isolated runtime install, and safely writes the verified local wheel under
`dist/`. Install that wheel into the sibling runtime without index access:

```text
# Windows:
../source-read-runtime/Scripts/python -m pip install --no-index --no-deps --force-reinstall dist/source_read-0.2.0-py3-none-any.whl
# POSIX:
../source-read-runtime/bin/python -m pip install --no-index --no-deps --force-reinstall dist/source_read-0.2.0-py3-none-any.whl
```

Generated wheels are not release artifacts; ZIP compression can produce
different wheel bytes across Python or zlib versions.
The initial Python interpreter, its bundled pip bootstrap, the package index,
and the hosted runner remain trust inputs; hash locking does not make upstream
code safe or provide operating-system network isolation.

## Recommended API

Use `assess_check()` when the caller has a boolean policy result. The required
`passed` argument determines the known machine exit code and supplies default
display text. Custom verdict text remains caller-controlled and uninterpreted;
source read failures still force `UNKNOWN` / exit `2`:

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
  recommended boolean-check API. `passed` must be a real `bool`, determines
  exit `0`/`1`, and supplies default text; custom verdict text stays opaque.
- `assess_sources(reads, verdict, *, verdict_exit_code=EXIT_PASS,
  allow_empty=False)` is the lower-level text/code API.
- `SourceAssessment` exposes immutable source membership, generic `problems`,
  unreadable indexes, requested/effective verdicts and codes, empty-input
  state, `unknown_applied`, and `is_pass` / `is_fail` / `is_unknown`.
- In this unreleased source candidate, `SourceAssessment.to_report()` exports
  a content-free machine report as described below.
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

## Unreleased integration: machine reports

`SourceAssessment.to_report()` is an additive API in this source candidate. It
is **not included in the published `v0.2.0` tag or source archive**. The current
package version is retained during preparation; a future release needs its own
reviewed version and immutable artifacts. Install the candidate's exact source
or locally verified wheel to evaluate this feature, not the older release.

Use the report between a reader and a CI check, policy engine, or agent tool
adapter when those consumers need the machine outcome but not source contents:

```python
import json
from source_read import SourceRead, assess_check

# The adapter, not this library, performed and classified the source operation.
assessment = assess_check([SourceRead.ok([], label="synthetic inventory")], passed=True)
print(json.dumps(assessment.to_report()))
raise SystemExit(assessment.exit_code)
```

The report is a fresh JSON-ready mapping with this exact initial shape:

```json
{
  "schema": "source-read.assessment.v1",
  "outcome": "PASS",
  "exit_code": 0,
  "source_count": 1,
  "unreadable_indexes": [],
  "empty_input": false,
  "allow_empty": false,
  "unknown_applied": false
}
```

`outcome` is derived only from the effective machine exit code: `0` means
`PASS`, `1` means `FAIL`, and `2` means `UNKNOWN`. Arbitrary caller verdict text
is not interpreted or copied. Source indexes are zero-based positions in the
materialized input, not source identities. `empty_input` records zero sources;
`allow_empty` makes an explicit empty-source policy exception visible.

The report omits payloads, labels, reasons, generic/detailed problem text, and
requested/effective caller verdict text. It never calls payload serialization
or string conversion. A caller can mutate the returned mapping or index list
without changing the assessment; a later report is independent of that mutation.
The schema identifies this projection's initial contract, not a universal agent
protocol. This API exports reports; it does not parse or validate received JSON.

Content-free does not mean universally safe to share. Counts and outcomes can
still reveal sensitive facts. Review recipients and logs, and do not treat a
report as authenticated, tamper-evident, independently observed, fresh, complete,
or proof of a source read. Those properties require their own integration.

### A reader and a CI consumer

The [HTTP reference adapter](examples/http_check.py) is a standard-library-only
example for a caller-controlled HTTP(S) JSON inventory. The policy is deliberately
simple: a full HTTP 200 response containing an empty list passes, an obtained
nonempty list fails, and HTTP/read/UTF-8/JSON/shape failure becomes unknown.
Partial HTTP 206 responses are not complete inventories. The adapter uses a
bounded read and timeout. It is not a crawler, network sandbox, pagination
implementation, or production SSRF boundary. The caller remains responsible
for whether a response represents the complete source required by its policy.

Run the example with the Python environment that has the candidate installed:

```text
python examples/http_check.py --url http://127.0.0.1:8080/inventory
python examples/http_check.py --no-sources
```

Each supported run writes only the content-free JSON report to stdout and exits
with that report's `exit_code`. The second command deliberately returns `2`.
An empty HTTP body, malformed JSON, or a server outage is not an empty inventory.
An integration must explicitly classify source acquisition and policy results;
for example, an MCP tool's domain rejection is not automatically a transport
failure. No MCP-specific classifier or dependency is added here.

`python scripts/verify_package_build.py` copies the example into a new temporary
consumer **outside the checkout**, installs the actual verified wheel in a
separate external runtime, and invokes it using Python `-I`. A synthetic loopback
server supplies empty, findings, unavailable, malformed, wrong-shape, and partial
response cases; a seventh case supplies zero sources. The check verifies installed module origin,
JSON and exit codes, privacy sentinels, wheel identity, and zero runtime
dependencies. It retains `dist/installed-consumer-acceptance.json`, including the
exact wheel/core/copied-consumer hashes. This demonstrates installed integration with a reader
and CI-style machine output, not external-user adoption or a live provider test.

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

GitHub's [release list](https://github.com/nymrel/source-read/releases) is the
authoritative record of published versions. The
[`v0.2.0` release page](https://github.com/nymrel/source-read/releases/tag/v0.2.0),
once published, records the exact tag target, source-archive digest,
independent review, hosted checks, and GitHub asset attestation. Tagged
documentation is immutable release-time evidence; the
[current main documentation](https://github.com/nymrel/source-read/blob/main/README.md)
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
