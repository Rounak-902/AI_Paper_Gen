export type ApiMeta = {
  requestId?: string;
  pagination?: unknown;
};

export type ApiSuccessEnvelope<T> = {
  success: true;
  data: T;
  message?: string;
  meta?: ApiMeta;
};

export type ApiErrorEnvelope = {
  success: false;
  error: {
    code: string;
    message: string;
    details?: unknown;
    ehRef?: string;
  };
  meta?: ApiMeta;
};

export type ApiEnvelope<T> = ApiSuccessEnvelope<T> | ApiErrorEnvelope;
