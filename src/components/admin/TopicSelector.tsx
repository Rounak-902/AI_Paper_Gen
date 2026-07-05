import { useState, useEffect } from 'react';
import { supabase } from '@/lib/supabaseClient';
import type { Topic } from '@/types/database';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Check, X, Loader2, Pin, ChevronDown } from 'lucide-react';

interface Props {
  chapterId: string;
  suggestedTopic: string | null;
  topicLoading: boolean;
  selectedTopicId: string | null;
  onAcceptSuggestion: () => Promise<string | null>;
  onRejectSuggestion: () => void;
  onSelectTopic: (id: string | null) => void;
}

export default function TopicSelector({
  chapterId,
  suggestedTopic,
  topicLoading,
  selectedTopicId,
  onAcceptSuggestion,
  onRejectSuggestion,
  onSelectTopic,
}: Props) {
  const [existingTopics, setExistingTopics] = useState<Topic[]>([]);
  const [showExisting, setShowExisting] = useState(false);
  const [manualTopic, setManualTopic] = useState('');
  const [showManual, setShowManual] = useState(false);

  useEffect(() => {
    if (chapterId) {
      supabase
        .from('topics')
        .select('*')
        .eq('chapter_id', chapterId)
        .order('topic_name')
        .then(({ data }) => {
          setExistingTopics((data as Topic[]) ?? []);
        });
    }
  }, [chapterId]);

  const handleAccept = async () => {
    const topicId = await onAcceptSuggestion();
    if (topicId) onSelectTopic(topicId);
  };

  const handleManualSubmit = async () => {
    if (!manualTopic.trim()) return;

    // Check if exists
    const existing = existingTopics.find(
      (t) => t.topic_name.toLowerCase() === manualTopic.toLowerCase(),
    );
    if (existing) {
      onSelectTopic(existing.topic_id);
      setManualTopic('');
      setShowManual(false);
      return;
    }

    // Create new
    const { data } = await supabase
      .from('topics')
      .insert({
        topic_name: manualTopic.trim(),
        chapter_id: chapterId,
      })
      .select('topic_id')
      .single();

    if (data) {
      onSelectTopic(data.topic_id);
      setManualTopic('');
      setShowManual(false);
    }
  };

  const selectedTopic = existingTopics.find((t) => t.topic_id === selectedTopicId);

  return (
    <div className="space-y-3">
      <Label className="text-[#64748B]">Topic (optional)</Label>

      {/* Selected topic display */}
      {selectedTopicId && selectedTopic && (
        <div className="flex items-center gap-2 rounded-lg bg-indigo-50 px-3 py-2">
          <Pin className="h-3 w-3 text-[#4F46E5]" />
          <span className="text-sm font-medium text-[#4F46E5]">
            {selectedTopic.topic_name}
          </span>
          <button
            onClick={() => onSelectTopic(null)}
            className="ml-auto text-[#64748B] hover:text-[#1E293B]"
          >
            <X className="h-3 w-3" />
          </button>
        </div>
      )}

      {/* Layer 1: AI suggestion */}
      {!selectedTopicId && (
        <>
          {topicLoading && (
            <div className="flex items-center gap-2 text-sm text-[#64748B]">
              <Loader2 className="h-3 w-3 animate-spin" />
              Suggesting topic...
            </div>
          )}

          {suggestedTopic && !topicLoading && (
            <div className="flex items-center gap-2 rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-2">
              <Pin className="h-3 w-3 text-[#4F46E5]" />
              <span className="text-sm font-medium text-[#4F46E5]">
                {suggestedTopic}
              </span>
              <div className="ml-auto flex gap-1">
                <button
                  onClick={handleAccept}
                  className="rounded bg-[#10B981] px-2 py-1 text-xs text-white hover:bg-emerald-600"
                >
                  <Check className="h-3 w-3" />
                </button>
                <button
                  onClick={onRejectSuggestion}
                  className="rounded bg-gray-200 px-2 py-1 text-xs text-[#64748B] hover:bg-gray-300"
                >
                  <X className="h-3 w-3" />
                </button>
              </div>
            </div>
          )}

          {/* Layer 2: Existing topics dropdown */}
          <button
            onClick={() => setShowExisting(!showExisting)}
            className="flex items-center gap-1 text-xs text-[#4F46E5] hover:text-indigo-700"
          >
            <ChevronDown className={`h-3 w-3 transition-transform ${showExisting ? 'rotate-180' : ''}`} />
            Or pick from existing topics ({existingTopics.length})
          </button>

          {showExisting && (
            <div className="max-h-32 space-y-1 overflow-y-auto rounded-lg border p-2">
              {existingTopics.length === 0 ? (
                <p className="text-xs text-[#64748B]">No topics yet</p>
              ) : (
                existingTopics.map((t) => (
                  <button
                    key={t.topic_id}
                    onClick={() => {
                      onSelectTopic(t.topic_id);
                      setShowExisting(false);
                    }}
                    className="w-full rounded px-2 py-1.5 text-left text-xs text-[#1E293B] hover:bg-gray-100"
                  >
                    {t.topic_name}
                  </button>
                ))
              )}
            </div>
          )}

          {/* Layer 3: Manual entry */}
          <button
            onClick={() => setShowManual(!showManual)}
            className="text-xs text-[#64748B] hover:text-[#1E293B]"
          >
            Or type a new topic name
          </button>

          {showManual && (
            <div className="flex gap-2">
              <Input
                value={manualTopic}
                onChange={(e) => setManualTopic(e.target.value)}
                placeholder="Enter topic name"
                className="h-8 text-sm"
                onKeyDown={(e) => e.key === 'Enter' && handleManualSubmit()}
              />
              <button
                onClick={handleManualSubmit}
                className="rounded bg-[#4F46E5] px-3 text-xs text-white hover:bg-indigo-700"
              >
                Add
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
