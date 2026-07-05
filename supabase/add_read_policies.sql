-- ============================================================
-- ADD READ POLICIES TO EXISTING TABLES
-- These tables have data but RLS is blocking the anon/auth key
-- Run this in the Supabase SQL Editor
-- ============================================================

-- 1. BOARDS - allow all authenticated users to read
ALTER TABLE boards ENABLE ROW LEVEL SECURITY;
CREATE POLICY "boards_read" ON boards
  FOR SELECT TO authenticated, anon
  USING (true);

-- 2. STREAM - allow all authenticated users to read
ALTER TABLE stream ENABLE ROW LEVEL SECURITY;
CREATE POLICY "stream_read" ON stream
  FOR SELECT TO authenticated, anon
  USING (true);

-- 3. CLASS - allow all authenticated users to read
ALTER TABLE class ENABLE ROW LEVEL SECURITY;
CREATE POLICY "class_read" ON class
  FOR SELECT TO authenticated, anon
  USING (true);

-- 4. SUBJECT - allow all authenticated users to read
ALTER TABLE subject ENABLE ROW LEVEL SECURITY;
CREATE POLICY "subject_read" ON subject
  FOR SELECT TO authenticated, anon
  USING (true);

-- 5. CHAPTERS - allow all authenticated users to read
ALTER TABLE chapters ENABLE ROW LEVEL SECURITY;
CREATE POLICY "chapters_read" ON chapters
  FOR SELECT TO authenticated, anon
  USING (true);

-- 6. TOPICS - allow all authenticated users to read
ALTER TABLE topics ENABLE ROW LEVEL SECURITY;
CREATE POLICY "topics_read" ON topics
  FOR SELECT TO authenticated, anon
  USING (true);
