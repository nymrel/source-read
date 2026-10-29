# Contributing

Thank you for helping improve Source Read.

Keep changes within the package's narrow truthfulness boundary: it records
caller-supplied source outcomes but performs no I/O, authentication,
authorization, source-integrity verification, or enforcement.

## Development workflow

1. Fork or clone the repository, fetch `main`, and create a focused branch.
2. Create a virtual environment **beside** the checkout, not inside it. For
   example, use `python -m venv ../source-read-development`.
3. Make one bounded change and add synthetic tests that observe its contract.
4. Run the complete validation set below from a clean working tree.
5. Open a pull request that explains behavior, risk, tests, and any skipped
   platform check. Do not merge around required checks.

A checkout-local `.venv` is gitignored for convenience but is still an extra
tree inside the exact release-verification boundary. The verifier intentionally
does not silently ignore it.

Run:

```text
python -m unittest discover -s tests -v
python scripts/verify_release.py
python scripts/build_source_archive.py --check-reproducible
python scripts/verify_release.py --archive dist/source-read-0.2.0.zip
python scripts/verify_package_build.py
```

The first four commands require only the standard library. The last command
downloads the exact SHA-256-locked build wheels, performs two offline-index
builds in disposable environments, compares them, and tests an isolated
runtime install. It does not publish the generated wheel.

Changes to `requirements/build.in` or `requirements/build.lock` require an
explicit dependency-update explanation. Review every version and artifact hash,
run the full supported CI matrix, and preserve the exact Hatchling pin in
`pyproject.toml`. A lock update establishes reviewed bytes, not that upstream
code is safe; security advisories and provenance still need review.

Tests, examples, issue reports, and commits must use synthetic data. Do not
submit customer data, secrets, private source, private paths, internal incident
details, or credentials. Security reports belong at the private route in
[SECURITY.md](SECURITY.md), not in a public issue.

Unless explicitly stated otherwise, contributions submitted for inclusion are
licensed under this repository's MIT license.
