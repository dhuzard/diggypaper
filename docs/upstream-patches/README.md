# Patches proposed to upstream (BENETNATH/diggypaper)

Prepared 2026-10-27 from the fork audit ([docs/audits/2026-10-27-fork-global-audit.md](../audits/2026-10-27-fork-global-audit.md), §5).
Base: upstream `main` at `5557435`. Each patch is one commit, independent of the others (no two touch
the same file), with its own regression test, and applies with `git am` alone or stacked. `SUMMARY.md`
records the exact checks run and every deviation from the fork's original fix; `U*.md` is the PR title
and body to paste.

| Patch | Fixes upstream | Size |
|---|---|---|
| `U1-resource-import-guard.patch` | Unix-only `resource` import breaks the benchmark worker and four test modules on Windows | 32+/2− |
| `U2-blind-list-own-review.patch` | blind articles list shows another reviewer's answers and name | 71+/15− |
| `U3-gold-from-reviews-project-scope.patch` | gold from reviews accepts article ids of another project | 52+/3− |
| `U4-fulltext-atomic-replace.patch` | JATS adoption can leave a stale PDF; absent methods inherit the previous version | 202+/13− |
| `U5-crate-human-evidence.patch` | RO-Crate drops annotation id, time, quotes and any reviewer reference | 109+/3− |
| `U6-pubmed-window-test-date-independent.patch` | `test_pubmed` window-splitting test passes or fails depending on the calendar date | 3+/1− |

To open the pull requests from a clone of the fork:

```bash
git fetch upstream main
for p in docs/upstream-patches/U*.patch; do
  b=$(basename "$p" .patch)
  git checkout -q -b "upstream/$b" upstream/main && git am "$p" && git push -u origin "upstream/$b"
  git checkout -q -
done
```

Then one PR per branch against `BENETNATH/diggypaper:main`, body from the matching `U*.md`. The same
fixes are already in the fork (commits `7cbfb78`, `ba0d589`, `2becddd`, `23c0cc5`, `0543510`, `b2dd65f`
and the merge `3e13113`).
