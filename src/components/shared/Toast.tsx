import { Toaster } from 'sonner';

export default function Toast() {
  return (
    <Toaster
      position="top-right"
      toastOptions={{
        style: {
          fontFamily: 'Inter, sans-serif',
          borderRadius: '8px',
        },
        classNames: {
          success: 'bg-[#10B981] text-white border-0',
          error: 'bg-[#EF4444] text-white border-0',
          info: 'bg-[#4F46E5] text-white border-0',
        },
      }}
      richColors
      closeButton
    />
  );
}
