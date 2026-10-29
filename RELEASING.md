# Releasing

Version 0.1.0 is the completed initial source-only release. No PyPI or other
package-registry publication is configured or authorized by this repository.

The release process is:

1. Run the complete local validation commands in `README.md` from a clean tree.
2. Build `dist/source-read-0.1.0.zip` reproducibly and record its SHA-256.
3. Obtain an independent review of that exact archive digest.
4. Open a pull request against `nymrel/source-read`, wait for the configured
   checks, and record explicit action-time maintainer approval in the release
   coordination record. When an eligible independent organization reviewer is
   available, also require provider-enforced peer approval.
5. Merge the reviewed commit, verify the public commit and hosted CI, then make
   an annotated `v0.1.0` source tag at that exact commit.
6. Verify the tag resolves to the reviewed commit and the old private donor
   repository and history remain absent.

The existing `v0.1.0` tag is immutable release evidence. Never move, replace,
or recreate it for later documentation, CI, or code changes; use a separately
reviewed new version for any future release.

The source tag is the supported release reference. Build archives are review
evidence, not registry packages. A GitHub Release object or package-registry
upload requires a separate decision and review.

Never import private donor history, private provenance, internal incidents,
credentials, customer data, or infrastructure configuration into the public
repository.
