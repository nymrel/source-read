# Releasing

Source Read uses reviewed source-only GitHub releases. No PyPI or other
package-registry publication is configured or authorized by this repository.
Generated wheels are validation artifacts and are not release assets.

## Version update checklist

Before review, update the version consistently in:

- `pyproject.toml` and `src/source_read/__init__.py`;
- `PROJECT_VERSION`, archive prefix, metadata checks, and workflow checks in
  `scripts/verify_release.py`;
- the default archive name in `scripts/build_source_archive.py`;
- CI smoke expectations and documentation validation commands; and
- release links and support statements.

Run the verifier after each version edit. It deliberately rejects partial
version updates and files outside the public allowlist.

## Release gates

For a release version `X.Y.Z`:

1. Start from a clean protected-branch candidate. Review the complete public
   tree, Git history, provenance statement, dependency-lock diff, and secret
   scan results. Do not import private donor history.
2. Run every command in `README.md`. Build
   `dist/source-read-X.Y.Z.zip` reproducibly and record its SHA-256. The locked
   package-build check must build twice and pass an isolated runtime install on
   the supported hosted matrix.
3. Obtain independent implementation and security review of the exact candidate
   commit, tree, and archive digest. Record limits and skipped checks honestly.
4. Open a pull request, wait for every required check, resolve conversations,
   and record explicit action-time maintainer approval in the release
   coordination record. An approval already given in the active release task
   may satisfy this gate only when its scope still covers the exact action and
   candidate; record its timestamp and scope. Do not describe task approval as
   a provider-enforced peer review. When an eligible independent organization
   reviewer is available, also require provider-enforced peer approval.
5. Merge only the reviewed candidate and verify the public commit, tree, hosted
   CI, CodeQL, and repository security posture. Rebuild the archive from that
   exact commit and confirm the same digest.
6. Create a protected annotated `vX.Y.Z` tag at that exact commit. Use a
   maintainer-controlled signing key when one is configured. Never invent or
   bootstrap an unreviewed release identity merely to make the tag appear
   signed.
7. Create an immutable GitHub Release containing only the reviewed source ZIP.
   Verify the release asset attestation binds the tag target and archive digest.
   If the tag is unsigned, disclose that fact and require all of these
   compensating controls: provider-verified target commit, protected `v*` tag
   rules with no bypass, immutable release, and verified asset attestation.
8. Re-download anonymously, verify the digest and archive, confirm the release
   is latest and immutable, and confirm no registry package was created.

The acquisition phase of `scripts/verify_package_build.py` uses the network to
fetch only hashes listed in `requirements/build.lock`. Its later `--no-index`
build phases prevent package-index fallback but are not an operating-system
network sandbox. The Python interpreter, bundled pip bootstrap, package index,
and hosted runner image remain trust inputs. Retain reviewed build wheels if a
future requirement calls for long-term offline rebuild availability.

## Dependency updates

`requirements/build.in` names the top-level build tools. The lock contains the
complete pinned Python 3.11+ build closure and SHA-256 of each accepted universal
wheel. Update it only in a dedicated reviewed change:

1. resolve from the top-level input in a disposable environment;
2. download only binary wheels and record their hashes;
3. review every version, dependency edge, license, provenance, and advisory;
4. run the entire Linux, Windows, and macOS matrix; and
5. verify that hash mode fails on a changed or missing artifact.

Schedule routine review of the frozen tools and use the same guarded path for
urgent security updates. Hashes prevent substitution; they do not establish
that an upstream release is trustworthy.

## Immutability

Existing tags and releases are evidence. Never move, replace, delete, or
recreate them for later documentation, CI, or code changes. Use a separately
reviewed new version. `v0.1.0` remains the immutable initial release; its
unsigned tag status is historical and is not changed retroactively.

Never publish private donor history, private provenance, internal incidents,
credentials, customer data, or infrastructure configuration.
