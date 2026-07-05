-- ============================================================
-- PaperGen — Textbook Ingestor Migration
-- Run this in Supabase SQL Editor
-- Adds columns needed for textbook question ingestion
-- ============================================================

-- 1. Add textbook_source_type column with CHECK constraint
ALTER TABLE question_bank
  ADD COLUMN IF NOT EXISTS textbook_source_type varchar
  CHECK (textbook_source_type IN ('exercise', 'miscellaneous', 'solved_example', 'examples', 'intext'));

-- 2. Add edition column
ALTER TABLE question_bank
  ADD COLUMN IF NOT EXISTS edition varchar;

-- 3. Index for textbook_source_type queries
CREATE INDEX IF NOT EXISTS idx_qb_textbook_source_type
  ON question_bank(textbook_source_type);
