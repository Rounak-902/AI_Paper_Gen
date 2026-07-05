import { useState, useCallback } from 'react';
import QuestionForm from '@/components/admin/QuestionForm';
import QuestionPreviewCard from '@/components/admin/QuestionPreviewCard';
import RecentQuestionsTable from '@/components/admin/RecentQuestionsTable';
import type { QuestionFormData, QuestionBankItem } from '@/types/database';

export default function AddQuestionPage() {
  const [form, setForm] = useState<QuestionFormData | null>(null);
  const [subjectName, setSubjectName] = useState('');
  const [chapterName, setChapterName] = useState('');
  const [topicName, setTopicName] = useState('');
  const [editQuestion, setEditQuestion] = useState<QuestionBankItem | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  const handleFormChange = useCallback((f: QuestionFormData) => {
    setForm(f);
  }, []);

  const handleQuestionSaved = () => {
    setRefreshKey((k) => k + 1);
  };

  const handleLoadQuestion = (q: QuestionBankItem) => {
    setEditQuestion(q);
  };

  return (
    <div>
      <h1 className="mb-6 text-2xl font-bold text-[#1E293B]">Add Question</h1>

      <div className="grid gap-8 lg:grid-cols-5">
        {/* LEFT: Form (40%) */}
        <div className="lg:col-span-2">
          <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
            <QuestionForm
              onFormChange={handleFormChange}
              onSubjectNameChange={setSubjectName}
              onChapterNameChange={setChapterName}
              onTopicNameChange={setTopicName}
              onQuestionSaved={handleQuestionSaved}
              editQuestion={editQuestion}
            />
          </div>
        </div>

        {/* RIGHT: Preview + Recent (60%) */}
        <div className="lg:col-span-3 space-y-6">
          {/* Live preview */}
          <div>
            <h3 className="mb-3 text-sm font-semibold uppercase text-[#64748B]">
              Live Preview
            </h3>
            {form ? (
              <QuestionPreviewCard
                form={form}
                subjectName={subjectName}
                chapterName={chapterName}
                topicName={topicName}
              />
            ) : (
              <div className="flex h-64 items-center justify-center rounded-xl border-2 border-dashed border-gray-200 bg-gray-50">
                <p className="text-sm text-[#64748B]">
                  Fill the form to see a live preview here
                </p>
              </div>
            )}
          </div>

          {/* Recent questions */}
          <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
            <RecentQuestionsTable
              key={refreshKey}
              onLoadQuestion={handleLoadQuestion}
            />
          </div>
        </div>
      </div>
    </div>
  );
}
