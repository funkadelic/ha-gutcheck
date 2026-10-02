# Contributing to Gut Check

Bug reports and pull requests are welcome.

## Reporting a problem

Open an issue at https://github.com/funkadelic/ha-gutcheck/issues and include:

- Your Gut Check and Home Assistant versions
- Which check misbehaved, and what you expected instead
- The check sensor's `last_error` attribute, if it has one
- A diagnostics file: **Settings > Devices & services > Gut Check**, three-dot menu, **Download diagnostics**

The diagnostics file hides your API key but keeps entity, device and area names and the last payloads sent, so read it before you attach it. Gut Check's own log lines leave names out and carry only keys and counts.

## Setting up

You need Python 3.14.

```bash
python3.14 -m venv .venv
.venv/bin/pip install -r requirements-test.txt
.venv/bin/pip install pre-commit && .venv/bin/pre-commit install
```

`requirements-test.txt` pins one exact Home Assistant release through `pytest-homeassistant-custom-component`. Don't loosen it to `>=`: pip would then be free to resolve an older Home Assistant and the suite would test the wrong one.

## Checks

Run these before you push. CI runs the same commands:

```bash
.venv/bin/pytest                                         # 100% line and branch coverage
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy                                           # strict
```

The pre-commit hook runs ruff on every commit and can rewrite files under you. Stage them again and recommit.

## What CI runs

These checks run on every pull request. The four marked required must pass before a merge.

| Check | What it does |
| --- | --- |
| `test` (required) | Installs the pinned test requirements, then runs ruff, mypy and pytest. It uploads coverage and test results to Codecov and runs a SonarQube scan, which is skipped on pull requests from forks because forks do not get the scan token. |
| `validate` (required) | Runs Home Assistant's hassfest on the integration's manifest, translations and structure. |
| `validate-hacs` (required) | Runs HACS's own repository validation. |
| `pr-title` (required) | Checks the title is a Conventional Commit whose subject starts with a lowercase letter and doesn't end with a period. |
| pre-commit.ci | Runs the same hooks as the local pre-commit setup. |

hassfest and HACS validation also run once a day against `main`, so a new rule in either tool shows up even when nobody has pushed.

Merging to `main` runs release-please, which keeps a release pull request open with the next version and changelog. Merging that pull request publishes the release and attaches `gutcheck.zip`, the file HACS installs.

## Writing tests

- Use the `pytest-homeassistant-custom-component` fixtures and real `homeassistant` imports. Don't hand-mock Home Assistant classes.
- Build fixtures from captured data. `tests/fixtures/captured/` holds real payloads and Jev responses. A made-up shape can pass while the real one breaks.
- When behaviour depends on order or timing, drive the real sequence (setup, first refresh, later refreshes) and assert between the steps.
- Mock the API client. The suite must never spend tokens.

CI doesn't run mutation testing. Run it by hand:

```bash
.venv/bin/pip install --group mutation
.venv/bin/mutmut run --max-children 4 'custom_components.gutcheck.<module>.*'
.venv/bin/mutmut results
```

## Code rules

- Keep files to about 200 lines and functions to a cognitive complexity of 15 or less. Split early.
- Constants live in `const.py`, or in `recipes/<recipe>_const.py` for one recipe's question and tuning.
- Log with a module-level `_LOGGER` and lazy `%s` formatting. Log keys, counts and domains only, never entity, device or area names or any other text taken from state.
- Unique IDs and Repairs issue IDs are stored in Home Assistant's registries. Changing their shape needs a migration.
- Gut Check never acts on locks, alarms, garage doors or covers, or anything carrying the user's critical label. A change that weakens that rule won't be merged.

## Pull requests

- Branch from `main` and open the pull request as a draft until it is ready.
- The title must be a Conventional Commit using `feat`, `fix`, `perf`, `refactor`, `docs`, `test`, `build`, `ci` or `chore`, with a subject that starts with a lowercase letter and doesn't end with a period. A check enforces this, because release-please builds the changelog and the version number from the squashed title.
- Keep each pull request to one change.
- Don't edit the version in `manifest.json` or `const.py`, and don't add release tags. release-please does both.
