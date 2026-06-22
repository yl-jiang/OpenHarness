import type { ReactNode } from 'react';

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  description?: string;
  confirmLabel?: string;
  danger?: boolean;
  loading?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

function DangerIcon() {
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
      <line x1="12" y1="9" x2="12" y2="13" />
      <line x1="12" y1="17" x2="12.01" y2="17" />
    </svg>
  );
}

function DefaultIcon() {
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="10" />
      <line x1="12" y1="8" x2="12" y2="12" />
      <line x1="12" y1="16" x2="12.01" y2="16" />
    </svg>
  );
}

export function ConfirmDialog({ open, title, description, confirmLabel = 'Confirm', danger, loading, onConfirm, onCancel }: ConfirmDialogProps) {
  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-[90] grid place-items-center bg-black/40 backdrop-blur-[2px]"
      onClick={onCancel}
    >
      <div
        className="bg-surface-1 border border-border rounded-xl w-full max-w-sm mx-4 shadow-xl shadow-black/20 animate-[dialog-in_0.18s_ease-out_both]"
        onClick={(e) => e.stopPropagation()}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
      >
        {/* Top accent bar */}
        <div className={`h-[3px] rounded-t-xl ${danger ? 'bg-danger/70' : 'bg-accent/50'}`} />

        <div className="px-6 pt-5 pb-4">
          {/* Icon + title */}
          <div className="flex items-start gap-3 mb-3">
            <div className={`flex-shrink-0 w-10 h-10 rounded-full grid place-items-center ${
              danger ? 'bg-danger/10 text-danger' : 'bg-accent/10 text-accent'
            }`}>
              {danger ? <DangerIcon /> : <DefaultIcon />}
            </div>
            <div className="pt-1.5 min-w-0">
              <h3 id="confirm-title" className="text-sm font-semibold text-text m-0 leading-snug">{title}</h3>
            </div>
          </div>

          {/* Description */}
          {description && (
            <p className="text-[13px] text-text-secondary m-0 mb-5 leading-relaxed pl-[52px]">{description}</p>
          )}

          {/* Actions */}
          <div className="flex items-center justify-end gap-2 pt-2">
            <button
              onClick={onCancel}
              disabled={loading}
              className="text-[12px] px-4 py-1.5 rounded-md border border-border bg-surface-2 text-text-secondary hover:text-text hover:bg-surface-3 cursor-pointer transition-colors disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              onClick={onConfirm}
              disabled={loading}
              className={`text-[12px] px-4 py-1.5 rounded-md border font-medium cursor-pointer transition-all disabled:opacity-50 ${
                danger
                  ? 'border-danger/30 bg-danger text-white hover:bg-danger/90 active:scale-[0.97]'
                  : 'border-border bg-surface-2 text-text-secondary hover:text-text hover:border-text-muted active:scale-[0.97]'
              }`}
            >
              {loading ? 'Deleting...' : confirmLabel}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
