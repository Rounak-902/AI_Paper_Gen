import { Button } from '@/components/ui/button';
import { exportToPDF, printPaper } from '@/lib/pdfExport';
import { Download, Printer, FileCheck, ArrowLeft, BookKey } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { toast } from 'sonner';

interface Props {
  status: string;
  showAnswerKey: boolean;
  onToggleAnswerKey: () => void;
  onMarkFinal: () => void;
  paperTitle: string;
}

export default function ExportButtons({
  status,
  showAnswerKey,
  onToggleAnswerKey,
  onMarkFinal,
  paperTitle,
}: Props) {
  const navigate = useNavigate();

  const handleDownloadPDF = async () => {
    try {
      await exportToPDF('paper-preview', `${paperTitle || 'question-paper'}.pdf`);
      toast.success('PDF downloaded successfully');
    } catch {
      toast.error('Failed to generate PDF');
    }
  };

  return (
    <div
      className="flex flex-wrap items-center gap-3"
      data-no-print
    >
      <Button
        variant="outline"
        onClick={() => navigate('/dashboard')}
        className="gap-2"
      >
        <ArrowLeft className="h-4 w-4" />
        Dashboard
      </Button>

      <div className="flex-1" />

      <Button
        variant="outline"
        onClick={onToggleAnswerKey}
        className={`gap-2 ${showAnswerKey ? 'bg-emerald-50 text-[#10B981] border-emerald-200' : ''}`}
      >
        <BookKey className="h-4 w-4" />
        {showAnswerKey ? 'Hide Answer Key' : 'Show Answer Key'}
      </Button>

      <Button
        variant="outline"
        onClick={handleDownloadPDF}
        className="gap-2"
      >
        <Download className="h-4 w-4" />
        Download PDF
      </Button>

      <Button
        variant="outline"
        onClick={() => printPaper('paper-preview', false)}
        className="gap-2"
      >
        <Printer className="h-4 w-4" />
        Print
      </Button>

      <Button
        variant="outline"
        onClick={() => printPaper('paper-preview', true)}
        className="gap-2"
      >
        <Printer className="h-4 w-4" />
        Print + Key
      </Button>

      {status !== 'final' && (
        <Button
          onClick={onMarkFinal}
          className="gap-2 bg-[#10B981] text-white hover:bg-emerald-600"
        >
          <FileCheck className="h-4 w-4" />
          Mark Final
        </Button>
      )}
    </div>
  );
}
