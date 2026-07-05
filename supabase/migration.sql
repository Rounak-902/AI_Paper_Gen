-- ============================================================
-- PaperGen — Database Migration
-- Run this in Supabase SQL Editor
-- ============================================================

-- 1. Profiles table (extends auth.users)
CREATE TABLE IF NOT EXISTS profiles (
  id          uuid PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  full_name   varchar,
  role        varchar DEFAULT 'teacher', -- 'teacher' | 'admin'
  created_at  timestamptz DEFAULT now()
);

-- 2. Question Bank
CREATE TABLE IF NOT EXISTS question_bank (
  question_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),

  -- Context FKs
  board_id       uuid REFERENCES boards(board_id),
  class_id       uuid REFERENCES class(class_id),
  stream_id      uuid REFERENCES stream(stream_id),
  subject_id     uuid REFERENCES subject(subject_id),
  chapter_id     uuid REFERENCES chapters(chapter_id),
  topic_id       uuid REFERENCES topics(topic_id),

  -- Question content
  question_text  text NOT NULL,
  answer_text    text,
  answer_hint    text,

  -- Classification
  question_type  varchar NOT NULL,
  marks          int NOT NULL,
  difficulty     varchar,

  -- MCQ / True-False fields
  options        jsonb,
  correct_option varchar,

  -- Fill in the Blanks
  blank_answer   varchar,

  -- Match the Following
  match_left     jsonb,
  match_right    jsonb,
  match_answer   jsonb,

  -- Assertion-Reason
  assertion      text,
  reason         text,

  -- Numerical / Long Answer steps
  solution_steps jsonb,

  -- Diagram reference
  diagram_url    varchar,

  -- Source metadata
  source         varchar DEFAULT 'ai_generated',
  book_name      varchar,
  exercise_no    varchar,
  question_no    varchar,
  page_no        int,

  -- Usage tracking
  times_used     int DEFAULT 0,
  is_active      bool DEFAULT true,
  created_at     timestamptz DEFAULT now(),
  created_by     uuid REFERENCES auth.users(id)
);

-- 3. Generated Papers
CREATE TABLE IF NOT EXISTS generated_papers (
  paper_id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at        timestamptz DEFAULT now(),
  created_by        uuid REFERENCES auth.users(id) NOT NULL,
  board_id          uuid REFERENCES boards(board_id),
  class_id          uuid REFERENCES class(class_id),
  stream_id         uuid REFERENCES stream(stream_id),
  subject_id        uuid REFERENCES subject(subject_id),
  paper_title       varchar,
  total_marks       int,
  duration          varchar,
  difficulty        varchar,
  paper_config      jsonb,
  selected_chapters jsonb,
  status            varchar DEFAULT 'draft',
  is_deleted        bool DEFAULT false
);

-- 4. Paper Questions
CREATE TABLE IF NOT EXISTS paper_questions (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  paper_id     uuid REFERENCES generated_papers(paper_id) ON DELETE CASCADE,
  question_id  uuid REFERENCES question_bank(question_id),
  section_name varchar,
  order_index  int,
  is_replaced  bool DEFAULT false,
  replaced_at  timestamptz,
  created_at   timestamptz DEFAULT now()
);

-- ============================================================
-- Row Level Security
-- ============================================================

ALTER TABLE profiles        ENABLE ROW LEVEL SECURITY;
ALTER TABLE question_bank   ENABLE ROW LEVEL SECURITY;
ALTER TABLE generated_papers ENABLE ROW LEVEL SECURITY;
ALTER TABLE paper_questions  ENABLE ROW LEVEL SECURITY;

CREATE POLICY "own profile"
  ON profiles FOR ALL USING (id = auth.uid());

CREATE POLICY "authenticated read question bank"
  ON question_bank FOR SELECT USING (auth.role() = 'authenticated');

CREATE POLICY "authenticated insert question bank"
  ON question_bank FOR INSERT WITH CHECK (auth.role() = 'authenticated');

CREATE POLICY "creator update question bank"
  ON question_bank FOR UPDATE USING (created_by = auth.uid());

CREATE POLICY "own papers"
  ON generated_papers FOR ALL USING (created_by = auth.uid());

CREATE POLICY "own paper questions"
  ON paper_questions FOR ALL
  USING (paper_id IN (
    SELECT paper_id FROM generated_papers WHERE created_by = auth.uid()
  ));

-- ============================================================
-- Indexes
-- ============================================================

CREATE INDEX IF NOT EXISTS idx_qb_subject    ON question_bank(subject_id);
CREATE INDEX IF NOT EXISTS idx_qb_chapter    ON question_bank(chapter_id);
CREATE INDEX IF NOT EXISTS idx_qb_topic      ON question_bank(topic_id);
CREATE INDEX IF NOT EXISTS idx_qb_type       ON question_bank(question_type);
CREATE INDEX IF NOT EXISTS idx_qb_marks      ON question_bank(marks);
CREATE INDEX IF NOT EXISTS idx_qb_difficulty ON question_bank(difficulty);
CREATE INDEX IF NOT EXISTS idx_qb_source     ON question_bank(source);
