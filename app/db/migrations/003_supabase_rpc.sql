-- Migration 003: Supabase RPC functions
-- These are PostgreSQL functions for Supabase. Run in the Supabase SQL editor.
-- They are NOT applied by the SQLite migration runner.

-- Recursive ancestor lookup for breadcrumb navigation
CREATE OR REPLACE FUNCTION get_ancestors(task_id_param TEXT)
RETURNS SETOF tasks AS $$
  WITH RECURSIVE ancestors AS (
    SELECT t.*
    FROM tasks t
    WHERE t.id = (SELECT parent_id FROM tasks WHERE id = task_id_param)
    UNION ALL
    SELECT t.*
    FROM tasks t
    INNER JOIN ancestors a ON t.id = a.parent_id
  )
  SELECT * FROM ancestors;
$$ LANGUAGE SQL STABLE;

-- Get all status history for a project (used by reports)
CREATE OR REPLACE FUNCTION get_project_status_history(project_id_param TEXT)
RETURNS SETOF status_history AS $$
  SELECT sh.*
  FROM status_history sh
  INNER JOIN tasks t ON sh.task_id = t.id
  WHERE t.project_id = project_id_param
  ORDER BY sh.changed_at DESC;
$$ LANGUAGE SQL STABLE;

-- Enable Row Level Security (optional — for multi-user scenarios)
-- ALTER TABLE tasks ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE board_columns ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE task_sessions ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE task_issues ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE status_history ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE task_tags ENABLE ROW LEVEL SECURITY;
