# Security policy

## Supported versions

The current `main` branch and the latest published `0.2.x` source tag are
supported. Until the first `0.2.x` tag is published, the immutable `v0.1.0`
source release remains supported. Pre-release branches may change without
notice.

## Report a security issue

Do not post secrets, private source, customer data, credentials, or exploit
details in a public issue. Prefer GitHub's
[private vulnerability reporting form](https://github.com/nymrel/source-read/security/advisories/new).
If that route is unavailable, send a concise report to `contact@nymrel.com`.
Include the affected version or commit, Python and operating-system versions,
a minimal synthetic reproducer, and the expected and observed behavior. No
response-time SLA is promised.

## Security boundary

This package is a truthfulness helper for a caller-supplied read result. It is
not access control, authentication, authorization, input validation, a sandbox,
source-integrity verification, or a guarantee that an enforcement point obeyed
the result.

The package cannot detect when a caller:

- labels a failed read as successful;
- omits a required source;
- supplies forged or stale data;
- chooses an inappropriate computed verdict; or
- uses `allow_empty=True` without a valid policy basis.

## Release tooling

The source-release verifier rejects symbolic links, reparse points, hardlinks,
and other non-regular public-tree entries. It snapshots a supplied ZIP through
one identity-checked open and requires the complete bytes to match the canonical
deterministic build. These controls prevent allowlisted paths from following
excluded files and prevent unreferenced bytes from hiding outside validated ZIP
entries.

The archive builder stages bytes in a fresh exclusive sibling, verifies them,
and atomically replaces the destination. It rejects linked output paths,
redirection in output-directory ancestors, non-regular destinations, and
attempts to write inside the source tree outside `dist/`, including alternate
path spellings detected by filesystem identity. A failed staged write or
pre-replacement verification therefore does not truncate an existing reviewed
archive or a hardlinked victim. The destination is checked again after atomic
replacement; a failure at that final check is reported but does not restore the
prior output, which is another reason to use an exclusively controlled output
directory and retain reviewed artifacts separately.

Packaging validation consumes the exact SHA-256-locked build closure in
`requirements/build.lock`, disables index access during each build, compares
two wheel builds, snapshots each wheel through one identity-checked open,
rejects duplicate archive and RECORD names, extra metadata headers, and
unreferenced ZIP-envelope bytes, verifies every recorded digest against that
snapshot, and installs the result into an isolated runtime environment. The
runtime package itself has no dependencies.

Passing the verifier does not prove that Git history is clean, that every
printable secret pattern is absent, or that hosted repository protections are
enabled. The release process separately reviews the complete public tree,
immutable digest, public commit history, provider checks, and tag.
Run verification and archive creation from immutable or exclusively controlled
source and output directories. Path, link, identity, staging, and snapshot
checks reduce static-link and partial-write risk, but pathname APIs cannot
eliminate every race against a concurrently malicious directory owner on every
supported operating system. Hash locking also does not prove upstream code is
safe, preserve artifact availability, or authenticate the interpreter, bundled
pip bootstrap, package index, and hosted runner image.

## Data handling

The package never logs or renders a stored `value`. Dataclass representations
also hide labels and reasons. Generic problem summaries are safe by default.
Labels and reasons remain caller-controlled and may contain paths, usernames,
query data, exception messages, or credentials. Calling `detailed_problems()`
is an explicit decision to reveal them; sanitize first. The method renders
control, format, surrogate, private-use, unassigned, line-separator, and
paragraph-separator characters as visible escapes to prevent multiline or
invisible-control log injection, but it does not redact sensitive printable
text.

`assess_check()` is the recommended integration API because its required
boolean outcome selects exit `0` or `1`. The lower-level `assess_sources()`
keeps arbitrary verdict text independent from its exit code and defaults that
code to `0`; it never interprets failure-looking text. In either API, callers
must use the effective exit code or explicit `is_*` properties. Ordinary object
truthiness and an empty source-problem tuple do not mean pass.

Unbounded or infinite iterables can consume memory because assessment
materializes the input once. Exceptions raised during iteration propagate.
Convert a genuine source-operation failure into `SourceRead.unreadable(...)`;
do not catch broad process-control exceptions merely to produce an assessment.
