import { WizardProvider, useWizard } from '@/context/WizardContext';
import { useAuth } from '@/context/AuthContext';
import { useGeminiGenerate } from '@/hooks/useGeminiGenerate';
import StepperBar from '@/components/wizard/StepperBar';
import StepBoard from '@/components/wizard/StepBoard';
import StepClass from '@/components/wizard/StepClass';
import StepStream from '@/components/wizard/StepStream';
import StepSubject from '@/components/wizard/StepSubject';
import StepConfig from '@/components/wizard/StepConfig';
import StepChapters from '@/components/wizard/StepChapters';
import StepPattern from '@/components/wizard/StepPattern';
import StepReview from '@/components/wizard/StepReview';
import GeneratingProgress from '@/components/wizard/GeneratingProgress';
import { Button } from '@/components/ui/button';
import { ArrowLeft, ArrowRight, Sparkles } from 'lucide-react';
import { toast } from 'sonner';
import { useState } from 'react';

function WizardContent() {
  const wizard = useWizard();
  const { user } = useAuth();
  const { generatePaper, sectionProgress, isGenerating, paperId, error } =
    useGeminiGenerate();
  const [showProgress, setShowProgress] = useState(false);

  const {
    currentStep,
    currentStepName,
    nextStep,
    prevStep,
    canProceed,
    paperTitle,
  } = wizard;

  const isLastStep = currentStepName === 'review';
  const isFirstStep = currentStep === 0;

  const handleGenerate = async () => {
    if (!user) {
      toast.error('You must be logged in');
      return;
    }
    setShowProgress(true);
    await generatePaper(wizard, user.id);
  };

  const renderStep = () => {
    switch (currentStepName) {
      case 'board': return <StepBoard />;
      case 'class': return <StepClass />;
      case 'stream': return <StepStream />;
      case 'subject': return <StepSubject />;
      case 'config': return <StepConfig />;
      case 'chapters': return <StepChapters />;
      case 'pattern': return <StepPattern />;
      case 'review': return <StepReview />;
      default: return null;
    }
  };

  return (
    <div>
      <StepperBar />
      <div className="min-h-[400px]">{renderStep()}</div>

      {/* Navigation */}
      <div className="mt-8 flex items-center justify-between border-t border-gray-200 pt-6">
        <Button
          variant="outline"
          onClick={prevStep}
          disabled={isFirstStep}
          className="gap-2"
        >
          <ArrowLeft className="h-4 w-4" />
          Back
        </Button>

        {isLastStep ? (
          <Button
            onClick={handleGenerate}
            disabled={!canProceed() || isGenerating}
            className="gap-2 bg-gradient-to-r from-[#4F46E5] to-purple-600 text-white hover:from-indigo-700 hover:to-purple-700"
          >
            <Sparkles className="h-4 w-4" />
            Generate Question Paper
          </Button>
        ) : (
          <Button
            onClick={nextStep}
            disabled={!canProceed()}
            className="gap-2 bg-[#4F46E5] text-white hover:bg-indigo-700"
          >
            Next
            <ArrowRight className="h-4 w-4" />
          </Button>
        )}
      </div>

      {/* Progress overlay */}
      {showProgress && (
        <GeneratingProgress
          paperTitle={paperTitle}
          sectionProgress={sectionProgress}
          isGenerating={isGenerating}
          paperId={paperId}
          error={error}
        />
      )}
    </div>
  );
}

export default function WizardPage() {
  return (
    <WizardProvider>
      <WizardContent />
    </WizardProvider>
  );
}
