CREATE TABLE IF NOT EXISTS audit_events (
  id TEXT PRIMARY KEY,
  trace_id TEXT NOT NULL,
  request_id TEXT NOT NULL,
  session_id_hash TEXT NOT NULL,
  user_id_hash TEXT NOT NULL,

  event_type TEXT NOT NULL,
  actor TEXT NOT NULL,
  tool_name TEXT,
  sequence INTEGER NOT NULL,

  input_redacted TEXT,
  output_redacted TEXT,
  pii_findings TEXT,
  policy_decision TEXT NOT NULL,

  event_timestamp TEXT NOT NULL,
  created_at TEXT NOT NULL,
  retention_until TEXT,

  prev_hash TEXT,
  event_hash TEXT NOT NULL,
  chain_index BIGSERIAL
);

CREATE INDEX IF NOT EXISTS idx_audit_trace_id
ON audit_events(trace_id);

CREATE INDEX IF NOT EXISTS idx_audit_session_id_hash
ON audit_events(session_id_hash);

CREATE INDEX IF NOT EXISTS idx_audit_created_at
ON audit_events(created_at);