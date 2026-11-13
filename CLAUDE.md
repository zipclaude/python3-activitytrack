# CLAUDE.md — notes for the assistant

The library of ActivityTrack on the PC (client, vault, update check), used by the cli, the tui and the desktop application. Work in progress lives in the GitLab issues and merge requests.

## 0. Branches and releases (read first)

The whole workflow is in
[activitytrack-doc/agents/workflow.md](https://gitlab.com/activitytrack/activitytrack-doc/-/blob/main/agents/workflow.md)
(usually cloned next to this repository as `../activitytrack-doc`). In short:

- `main` is the **last released version**, `next` is the development branch. Work happens
  in `feature/<name>` or `issue/<n>-<name>`, cut from `next` and merged back into it with a
  merge request. Nobody pushes to `main` or `next`.
- Stable release: merge request `next` → `main`, merged fast-forward only; tag `vX.Y.Z` on
  `main`; then merge `main` into `next`.
- Beta: tag `vX.Y.Z-beta-N` on `next` (X.Y.Z is the *next* version); Debian version
  `X.Y.Z~beta-N-1` (the CI turns the tag's first hyphen into a tilde,
  the changelog adds the revision). Needs the unit tests and the local Wine jobs, not the paid Windows pipeline.
- Security patch: `security/<name>` cut from `main`, version `X.Y.Z.1`, released at once with
  the security report, then merged into `next`.
- Test locally first. Pipelines on GitLab's shared runners start only when the owner says so;
  jobs with `tags: [local]` run on the owner's runner (see `agents/local-ci.md` in the
  documentation repository).
- The Android application id, the AppStream ids and the package names keep the
  `it.infraconsulting` names; they do not follow the GitLab group.

## 1. Working rules

- English for code, comments, commits and docs; Italian with the maintainer.
- Indentation as the surrounding code. No secrets, tokens or personal data in the repository: it is public.
- Commits end with the `Co-Authored-By:` line naming the model actually used.

## 2. Tests

```sh
flake8 activitytrack tests scripts
python3 scripts/strings.py --check
python3 -m unittest discover -s tests -t .
```
The job `windows` of `.gitlab-ci.yml` runs the same tests on Windows (paid shared runner, on command).

## 3. A release touches

- `activitytrack/__init__.py` (version), `debian/changelog` (`X.Y.Z-1`; a beta is `X.Y.Z~beta-N-1`).
- `CHANGELOG.md` (upstream changelog, installed by `dh_installchangelogs`): the section of the release, only changes in behaviour, compatibility and features. Bug fixes are not listed one by one: a single line "Various bug fixes." per release; only security fixes are listed, under "Security", with their issue number.
- The tag `vX.Y.Z` is what the cli, tui and desktop name in `.gitlab/lib-ref`: release the library **first**.

## 4. GitHub backup

GitHub (`zipclaude/python3-activitytrack`) is a backup of the GitLab project. The workflow in `.github/workflows/ci.yml` mirrors `.gitlab-ci.yml`: keep them in step. Nothing runs on its own there: start it with "Run workflow" on a branch or tag, or push a branch `ghtest/<name>` to try a change. The job `testvm` stays GitLab-only (it needs the owner's test VM runner).
