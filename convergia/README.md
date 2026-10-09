# Convergia Jasmin maintenance

Shared engine source: https://github.com/convergia/jasmin.
Upstream: https://github.com/jookies/jasmin.

This fork uses manual development and qualification, matching the existing
Convergia project process. GitHub Actions are disabled. Inherited upstream
workflow files are preserved unchanged; no new workflow files are published.

## Source and ownership

- master preserves the upstream snapshot/history. Do not deploy it.
- convergia is the default maintenance branch. Use feature branches and PRs.
- Initial engine source: eb75667381ba7f9859456717130c348a8dfbb653, Jasmin 0.11.1.
- The existing lab DLR correction is retained in source. See patches.md.
  All 101 Python package files match the measured lab package as text.
- No new reliability fixes are included. The source-built candidate reproduced
  17 PASS, 7 FAIL, cleanup PASS, production NOT qualified.
- Convergia_SMPP retains deployments, provider configuration, dashboard,
  qualification ownership and acceptance evidence. This public fork contains
  no supplier credentials or operational account configuration.

## Build and qualify manually

Prerequisites: GitHub CLI for upstream tracking, Python 3.10+, and a local
Linux Docker engine with Compose v2. From this repository root:

```powershell
$candidateCommit = git rev-parse HEAD
docker build --build-arg "SOURCE_COMMIT=$candidateCommit" -f convergia/Dockerfile -t convergia-jasmin-candidate:local .
python convergia/test_upstream_watch.py
python convergia/qualification/scripts/test-gateway-qualification.py
python convergia/qualification/scripts/qualify-gateway.py
```

On Linux/macOS pass the output of git rev-parse HEAD as SOURCE_COMMIT.
Use a clean reviewed checkout. Record exact commit and image identity with
qualification evidence. The image defaults to help, requiring explicit
configuration for service use. prepare.py records provenance without changing
installed engine code.

The controller creates a private random Docker project with synthetic accounts,
no published ports, no host mounts and no live provider settings. Reports go
under convergia/qualification/archive/. Exit 1 is measured gateway failure;
exit 2 is blocked infrastructure. Partial runs never qualify.

Python packages are pinned in convergia/requirements.txt and the Python base
image by digest. Fixture npm packages use package-lock.json. Fixture apt packages
resolve at build time. This is controlled provenance, not complete byte-for-byte
reproducibility. Old pins need security remediation, not blind upgrades.

Install pip-audit==2.9.0 in a separate tooling virtual environment, then run:

```powershell
pip-audit --no-deps --disable-pip -r convergia/requirements.txt --format json --output convergia/dependency-audit.json
```

The initial audit found 24 known vulnerabilities across 7 pinned packages.
Assess the findings; they do not establish exploitability or cover all OS,
RabbitMQ or Redis vulnerabilities.

## Check upstream manually

```powershell
python convergia/upstream_watch.py
python convergia/upstream_watch.py --publish
```

The first command observes only. The second records a deduplicated review issue
in this fork using ordinary GitHub CLI repository access. It requires no new
workflow permission and does not retrieve/display credential values.
Snapshot categories include default-branch HEAD, all tags/releases, selected
reliability issues/PRs and published repository security advisories. Reversions
are detected. API failures fail the command instead of claiming no changes.
Closing a reviewed issue does not recreate it for the same observation.

The first issue is an observation baseline, not approval of upstream HEAD.
Compare against our older maintained source. Review weekly and before each
candidate build; prioritize security and message-loss fixes. There is no
continuous polling or guaranteed alert delivery. GitHub issue notifications
follow operator subscriptions. Scheduled monitoring is a future decision.

## Patch ownership and release gates

Keep patches small with upstream references and reproducing regressions.
Review upstream fixes first, contribute suitable fixes back, and retire patches
when equivalent upstream code passes qualification. Preserve LICENSE and
attribution. Assign an engineering maintainer before production, with Tom Gilley
as platform owner.

Convergia_SMPP owns the canonical qualification harness. This fork includes a
sanitized snapshot from ad8ef61, with hashes/adaptations in harness-provenance.json.
Sync changes through reviewed PRs. Never silently weaken assertions. The Console
extraction omits cloud operations and secret readers.

Manual promotion requires a full passing suite, reviewed security findings,
capacity/recovery tests and real supplier acceptance. Publish only a reviewed
qualified commit, and deploy by immutable image digest, never a mutable latest
tag. No image publication or live deployment is automated by this setup.
