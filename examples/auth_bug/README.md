This fixture demonstrates transitive import selection: `middleware.py` imports `session.py`, and only the import edge surfaces the latter for an "authentication bug" task.
It also holds a real configuration discrepancy (60-minute session timeout in `config/auth.yaml` vs. the 30-minute requirement in `docs/security.md`) and a test coverage gap (`tests/test_auth.py` covers login/logout but never session expiration).
Filler files under `billing/`, `reports/`, `utils/`, `notifications/`, and `inventory/` are unrelated to authentication and exist to make exclusion measurable.
