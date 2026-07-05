import {
  createContext,
  useContext,
  useState,
  useCallback,
  type ReactNode,
} from 'react';
import type { SectionConfig, WizardState } from '../types/database';

const DEFAULT_STATE: WizardState = {
  currentStep: 0,
  boardId: null,
  boardName: null,
  classId: null,
  className: null,
  needsStream: false,
  streamId: null,
  streamName: null,
  subjectId: null,
  subjectName: null,
  paperTitle: '',
  totalMarks: 80,
  duration: '3hr',
  difficulty: 'Mixed',
  selectedChapterIds: [],
  selectedChapterNames: [],
  sections: [],
};

// Step names for the wizard
type StepName = 'board' | 'class' | 'stream' | 'subject' | 'config' | 'chapters' | 'pattern' | 'review';

interface WizardContextType extends WizardState {
  setBoard: (id: string, name: string) => void;
  setClass: (id: string, name: string, needsStream: boolean) => void;
  setStream: (id: string, name: string) => void;
  setSubject: (id: string, name: string) => void;
  setPaperConfig: (config: Partial<Pick<WizardState, 'paperTitle' | 'totalMarks' | 'duration' | 'difficulty'>>) => void;
  setChapters: (ids: string[], names: string[]) => void;
  setSections: (sections: SectionConfig[]) => void;
  nextStep: () => void;
  prevStep: () => void;
  goToStep: (step: number) => void;
  resetWizard: () => void;
  canProceed: () => boolean;
  totalSteps: number;
  steps: StepName[];
  currentStepName: StepName;
}

const WizardContext = createContext<WizardContextType | null>(null);

function classNeedsStream(className: string): boolean {
  const upper = className.toUpperCase();
  return (
    upper.includes('11') ||
    upper.includes('12') ||
    upper.includes('XI') ||
    upper.includes('XII')
  );
}

function getSteps(needsStream: boolean): StepName[] {
  if (needsStream) {
    return ['board', 'class', 'stream', 'subject', 'config', 'chapters', 'pattern', 'review'];
  }
  return ['board', 'class', 'subject', 'config', 'chapters', 'pattern', 'review'];
}

export function WizardProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<WizardState>({ ...DEFAULT_STATE });

  const steps = getSteps(state.needsStream);
  const totalSteps = steps.length;
  const currentStepName = steps[state.currentStep] ?? 'board';

  const setBoard = useCallback((id: string, name: string) => {
    setState((prev) => ({
      ...prev,
      boardId: id,
      boardName: name,
      classId: null,
      className: null,
      needsStream: false,
      streamId: null,
      streamName: null,
      subjectId: null,
      subjectName: null,
      selectedChapterIds: [],
      selectedChapterNames: [],
    }));
  }, []);

  const setClass = useCallback((id: string, name: string, needsStream: boolean) => {
    setState((prev) => ({
      ...prev,
      classId: id,
      className: name,
      needsStream,
      streamId: null,
      streamName: null,
      subjectId: null,
      subjectName: null,
      selectedChapterIds: [],
      selectedChapterNames: [],
    }));
  }, []);

  const setStream = useCallback((id: string, name: string) => {
    setState((prev) => ({
      ...prev,
      streamId: id,
      streamName: name,
    }));
  }, []);

  const setSubject = useCallback((id: string, name: string) => {
    setState((prev) => ({
      ...prev,
      subjectId: id,
      subjectName: name,
      paperTitle: `${name} Question Paper – Class ${prev.className} – ${prev.boardName}`,
      selectedChapterIds: [],
      selectedChapterNames: [],
    }));
  }, []);

  const setPaperConfig = useCallback(
    (config: Partial<Pick<WizardState, 'paperTitle' | 'totalMarks' | 'duration' | 'difficulty'>>) => {
      setState((prev) => ({ ...prev, ...config }));
    },
    [],
  );

  const setChapters = useCallback((ids: string[], names: string[]) => {
    setState((prev) => ({
      ...prev,
      selectedChapterIds: ids,
      selectedChapterNames: names,
    }));
  }, []);

  const setSections = useCallback((sections: SectionConfig[]) => {
    setState((prev) => ({ ...prev, sections }));
  }, []);

  const nextStep = useCallback(() => {
    setState((prev) => {
      const stepsArr = getSteps(prev.needsStream);
      return { ...prev, currentStep: Math.min(prev.currentStep + 1, stepsArr.length - 1) };
    });
  }, []);

  const prevStep = useCallback(() => {
    setState((prev) => ({
      ...prev,
      currentStep: Math.max(prev.currentStep - 1, 0),
    }));
  }, []);

  const goToStep = useCallback((step: number) => {
    setState((prev) => ({ ...prev, currentStep: step }));
  }, []);

  const resetWizard = useCallback(() => {
    setState({ ...DEFAULT_STATE });
  }, []);

  const canProceed = useCallback((): boolean => {
    const s = state;
    const stepName = getSteps(s.needsStream)[s.currentStep];
    switch (stepName) {
      case 'board':
        return !!s.boardId;
      case 'class':
        return !!s.classId;
      case 'stream':
        return !!s.streamId;
      case 'subject':
        return !!s.subjectId;
      case 'config':
        return !!s.paperTitle && s.totalMarks >= 10 && !!s.duration;
      case 'chapters':
        return s.selectedChapterIds.length > 0;
      case 'pattern':
        return s.sections.length > 0;
      case 'review':
        return true;
      default:
        return true;
    }
  }, [state]);

  return (
    <WizardContext.Provider
      value={{
        ...state,
        setBoard,
        setClass,
        setStream,
        setSubject,
        setPaperConfig,
        setChapters,
        setSections,
        nextStep,
        prevStep,
        goToStep,
        resetWizard,
        canProceed,
        totalSteps,
        steps,
        currentStepName,
      }}
    >
      {children}
    </WizardContext.Provider>
  );
}

export function useWizard(): WizardContextType {
  const context = useContext(WizardContext);
  if (!context) {
    throw new Error('useWizard must be used within a WizardProvider');
  }
  return context;
}

export { classNeedsStream };
export default WizardContext;
