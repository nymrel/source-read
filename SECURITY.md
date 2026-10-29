# Security policy

## Supported versions

The current `main` branch and the latest `0.1.x` source tag are supported.
Pre-release branches may change without notice.

## Report a security issue

Do not post secrets, private source, customer data, credentials, or exploit
details in a public issue. Send a concise report to `contact@nymrel.com` with
the affected version or commit, Python and operating-system versions, a minimal
synthetic reproducer, and the expected and observed behavior. No response-time
SLA is promised.

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

The source-release verifier rejects links and other non-regular public-tree
entries, and it requires a supplied ZIP to match the canonical deterministic
build byte for byte. These controls prevent allowlisted paths from following
excluded files and prevent unreferenced bytes from hiding outside validated ZIP
entries.

Passing the verifier does not prove that Git history is clean, that every
printable secret pattern is absent, or that hosted repository protections are
enabled. The release process separately reviews the complete public tree,
immutable digest, public commit history, provider checks, and tag.

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

Unbounded or infinite iterables can consume memory because assessment
materializes the input once. Exceptions raised during iteration propagate.
Convert a genuine source-operation failure into `SourceRead.unreadable(...)`;
do not catch broad process-control exceptions merely to produce an assessment.
