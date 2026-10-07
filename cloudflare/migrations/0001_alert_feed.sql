CREATE TABLE IF NOT EXISTS alert_feed (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  sequence INTEGER NOT NULL,
  generated_at TEXT NOT NULL,
  revision TEXT NOT NULL,
  payload TEXT NOT NULL,
  received_at TEXT NOT NULL
);
