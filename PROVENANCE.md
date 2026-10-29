# Provenance statement

This project is a clean, generic rewrite of a small in-house truthfulness
primitive. It is not a byte-for-byte export, and the private donor repository
or its Git history is not part of this project. Private anecdotes, internal
names, paths, counts, operational references, and embedded historical examples
were deliberately excluded. Tests and documentation use synthetic data only.

The rewrite changes the API to address two review findings:

1. separate iterable passes could exhaust a generator and permit a false clean
   verdict; and
2. zero supplied sources could silently preserve a clean verdict.

Development was AI-assisted. The maintainer reviewed and accepted the final
implementation and authorized its release by Nymrel under the MIT license. The
bounded provenance review found no incorporated third-party source or notices.

The project has no runtime dependencies beyond the Python standard library.
Its pinned build backend and CI actions are release tooling, not runtime
dependencies. Exact private-source lineage and the authorization receipt are
retained in an archive-excluded internal review record.
