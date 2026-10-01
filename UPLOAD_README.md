# MOZES v2B hotfix

The v2B upload reached `main`, but CI failed because the existing payload test still expected version `0.3.0` while v2B intentionally bumped the public payload to `0.3.1`.

Also, the three `.github/workflows/*.yml` files from v2B were not included in the GitHub upload, so the live-price refresh and automatic Pages-after-monitor behavior are not active yet.

Upload/replace exactly these four files:

- `tests/test_app_v3.py`
- `.github/workflows/lightweight-monitor.yml`
- `.github/workflows/pages.yml`
- `.github/workflows/nightly.yml`

After commit:
1. Wait for CI and Pages.
2. Run `lightweight-live-monitor` manually once.
3. Confirm a new `deploy-pages` run starts automatically after the monitor succeeds.

Do not change any other v2B files in this hotfix.
