# Contributing

Thank you for helping improve Source Read.

Keep changes within the package's narrow truthfulness boundary: it records
caller-supplied source outcomes but performs no I/O, authentication,
authorization, source-integrity verification, or enforcement.

Before proposing a change, run:

```text
python -m unittest discover -s tests -v
python scripts/verify_release.py
python scripts/build_source_archive.py --check-reproducible
python scripts/verify_release.py --archive dist/source-read-0.1.0.zip
```

Tests, examples, issue reports, and commits must use synthetic data. Do not
submit customer data, secrets, private source, private paths, internal incident
details, or credentials. Security reports belong at the private route in
[SECURITY.md](SECURITY.md), not in a public issue.

Unless explicitly stated otherwise, contributions submitted for inclusion are
licensed under this repository's MIT license.
