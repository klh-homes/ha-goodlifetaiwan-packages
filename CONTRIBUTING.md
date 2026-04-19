# Contributing

All issues, pull requests, and comments must be in English.

## Development setup

```bash
git clone https://github.com/klh-homes/ha-goodlifetaiwan-packages.git
cd ha-goodlifetaiwan-packages
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pre-commit install
```

Python ≥ 3.12. Home Assistant ≥ 2024.1.0 (installed via the dev requirements).

## Running tests

```bash
pytest tests/ -v
```

66 tests cover the HTTP client, JWT helpers, auth state machine, coordinator diff/events, config flow, reauth, service handlers and concurrency. All use mocked HTTP via `aioresponses` plus the `hass` fixture from `pytest-homeassistant-custom-component`.

## Lint & format

```bash
ruff check custom_components/ tests/ scripts/
ruff format custom_components/ tests/ scripts/
```

`ruff.toml` pins the rule set (`F`, `I`, `E`, `W`, `B`, `UP`). The pre-commit hook runs both commands on every commit.

## Live smoke test

For manual end-to-end testing against the real `life-spi.glf.tw` API:

```bash
python scripts/smoke_test.py refresh              # rotate tokens
python scripts/smoke_test.py me                   # member info
python scripts/smoke_test.py unpicked             # list packages
python scripts/smoke_test.py qr qr.png            # generate pickup QR
python scripts/smoke_test.py detail <packageId>   # extended detail
python scripts/smoke_test.py login +886912345678  # full SMS login
```

Token files default to `../package-reverse-engineering/{token,refresh_token}.txt`. Override the directory with `GLT_TOKEN_DIR`. Each refresh call issues a new refresh token and the script writes the latest one back (the prior token stays valid until its 90-day `exp`, but keeping the stored one fresh is good hygiene — matches `check_packages.sh` behaviour).

See [`docs/reverse-engineering.md`](./docs/reverse-engineering.md) for where the API fingerprint came from.

## Testing against a local HA instance

The cleanest loop is to symlink the integration into a throwaway HA dev instance:

```bash
ln -s $(pwd)/custom_components/goodlifetaiwan /path/to/dev-ha/config/custom_components/goodlifetaiwan
```

Restart HA after every code change. Do **not** symlink into the same HA you rely on day-to-day during development — token rotation means a broken dev build can kick the prod install into `auth_needed`.

## Commit style

- Atomic commits. One logical change per commit.
- Present-tense imperative summary line, under 72 chars.
- Explain _why_ in the body when it isn't obvious from the diff.

## Pull requests

Run `pytest` and `ruff check` locally before opening a PR. CI runs the same matrix across Python 3.12 / 3.13 / 3.14 plus hassfest + HACS validation.

Update `CHANGELOG.md`'s `[Unreleased]` section in the same PR when you change user-visible behaviour (events, services, entity shapes, config flow steps).

## Reporting bugs

Issues should include:

- HA version (`Settings → About`)
- Integration version (`manifest.json`)
- Relevant log snippets (`Settings → System → Logs`, filter by `goodlifetaiwan`)
- Phone number format is sensitive — don't paste full numbers into public issues; the integration masks to last 4 digits in all its own logs

## Security issues

See [`SECURITY.md`](./SECURITY.md).
