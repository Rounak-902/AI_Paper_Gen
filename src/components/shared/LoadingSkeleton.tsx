import { cn } from '@/lib/utils';

interface LoadingSkeletonProps {
  variant?: 'card' | 'row' | 'text' | 'circle';
  className?: string;
  count?: number;
}

function SkeletonPulse({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        'animate-pulse rounded-md bg-gradient-to-r from-gray-200 via-gray-100 to-gray-200 bg-[length:200%_100%]',
        className,
      )}
    />
  );
}

export default function LoadingSkeleton({
  variant = 'card',
  className,
  count = 1,
}: LoadingSkeletonProps) {
  const items = Array.from({ length: count }, (_, i) => i);

  if (variant === 'text') {
    return (
      <div className={cn('space-y-2', className)}>
        {items.map((i) => (
          <SkeletonPulse key={i} className="h-4 w-full" />
        ))}
      </div>
    );
  }

  if (variant === 'row') {
    return (
      <div className={cn('space-y-3', className)}>
        {items.map((i) => (
          <div key={i} className="flex items-center gap-3">
            <SkeletonPulse className="h-10 w-10 rounded-full" />
            <div className="flex-1 space-y-2">
              <SkeletonPulse className="h-4 w-3/4" />
              <SkeletonPulse className="h-3 w-1/2" />
            </div>
          </div>
        ))}
      </div>
    );
  }

  if (variant === 'circle') {
    return (
      <div className={cn('flex gap-4', className)}>
        {items.map((i) => (
          <SkeletonPulse key={i} className="h-12 w-12 rounded-full" />
        ))}
      </div>
    );
  }

  // card variant
  return (
    <div className={cn('grid gap-4 sm:grid-cols-2 lg:grid-cols-3', className)}>
      {items.map((i) => (
        <div
          key={i}
          className="rounded-lg border border-gray-100 bg-white p-5 shadow-sm"
        >
          <SkeletonPulse className="mb-3 h-5 w-2/3" />
          <SkeletonPulse className="mb-2 h-4 w-full" />
          <SkeletonPulse className="mb-2 h-4 w-4/5" />
          <SkeletonPulse className="h-3 w-1/3" />
        </div>
      ))}
    </div>
  );
}
