-- Build 11: shared login throttling state (apply to development Neon before testing).
CREATE TABLE IF NOT EXISTS auth_login_limits (
  key TEXT PRIMARY KEY,
  attempts INTEGER NOT NULL DEFAULT 0,
  window_start TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS auth_login_limits_window_start_idx ON auth_login_limits(window_start);
