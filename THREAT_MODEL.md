# Threat model

## Assets

- truthful distinction between known and unknown outcomes;
- confidentiality of source values, labels, and failure reasons;
- predictable behavior for empty, mixed, and one-shot inputs;
- availability of the calling check.

## Trust boundaries

The caller is trusted to perform the source operation, enumerate every required
source, choose the initial verdict and exit code, and report success or failure
honestly. The helper begins only after those decisions. Any log, telemetry,
policy engine, or executor that consumes the assessment is outside this package.

## Addressed threats

| Threat | Control |
| --- | --- |
| A one-shot iterable is consumed by one helper and appears clean to another | One `assess_sources()` call materializes once and returns all derived state. |
| No source records accidentally imply success | Zero records default to `UNKNOWN` / exit `2`. |
| Empty source content is mistaken for read failure | Readability is explicit and independent of `value`. |
| Contradictory record state | Direct construction rejects readable-with-reason and unreadable-without-reason states. |
| A caller constructs a contradictory assessment | Assessment objects have no public constructor and are produced in one place. |
| Diagnostics disclose source contents | Values, labels, and reasons are hidden from default representations; generic problems omit details. |
| Caller-controlled verdict text injects default representations | Verdict strings are hidden from the default assessment representation. |
| Detailed text injects control or invisible format characters into logs | Control/format and Unicode line/paragraph separator characters are rendered as visible escapes. |
| An unreadable source preserves a success exit code | Unknown assessments force exit `2`. |
| A known outcome uses the reserved unknown exit code | Requested known-outcome codes are restricted to pass `0` or failure `1`. |
| A known outcome uses the reserved unknown verdict | Requested verdict text cannot be the reserved literal `UNKNOWN`. |

## Residual risks and non-goals

- `allow_empty=True` can still be misused; review it as a policy exception.
- The helper cannot know how many sources should exist or whether one was
  omitted. A caller needing that guarantee must compare against an expected
  source set before assessment.
- Stored `value`, `label`, and `reason` attributes remain accessible to code.
- Explicit detailed diagnostics can disclose sensitive printable text even
  though control characters are escaped.
- Infinite or attacker-controlled iterables can exhaust resources.
- Iterator exceptions propagate, and partial results are not returned.
- The helper does not authenticate, authorize, sandbox, retry, redact arbitrary
  text, verify freshness, or execute an enforcement decision.
