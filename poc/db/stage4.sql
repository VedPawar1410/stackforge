-- Stage 4: AI-Assisted Development
-- Adds stage4_status to projects, task_sessions for agent runs, task_usage for token tracking

ALTER TABLE projects ADD COLUMN IF NOT EXISTS stage4_status TEXT DEFAULT 'idle';

CREATE TABLE IF NOT EXISTS task_sessions (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id      UUID REFERENCES tasks(id),
    project_id   UUID REFERENCES projects(id),
    status       TEXT DEFAULT 'pending',     -- pending | running | completed | failed
    workspace_path TEXT,
    session_summary TEXT,                    -- claude CLI stdout (final response text)
    generated_files JSONB DEFAULT '[]',      -- [{path, size_bytes}] scanned after run
    started_at   TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    error_message TEXT,
    exit_code    INTEGER
);

CREATE INDEX IF NOT EXISTS task_sessions_project_idx ON task_sessions(project_id);
CREATE INDEX IF NOT EXISTS task_sessions_task_idx    ON task_sessions(task_id);

-- Mirrors stage*_metrics pattern. Tokens always 0 in Phase 1 (CLI does not expose them)
CREATE TABLE IF NOT EXISTS task_usage (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id         UUID,
    project_id      UUID,
    client_id       UUID,
    step            TEXT,
    model           TEXT DEFAULT 'claude-code-cli',
    stage           TEXT DEFAULT '4',
    task_type       TEXT,
    input_tokens    INTEGER DEFAULT 0,
    output_tokens   INTEGER DEFAULT 0,
    thinking_tokens INTEGER DEFAULT 0,
    duration_ms     INTEGER,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS task_usage_project_idx ON task_usage(project_id);
