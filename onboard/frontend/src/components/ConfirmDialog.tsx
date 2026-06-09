import type { ReactNode } from 'react';

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  description?: string;
  confirmLabel?: string;
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmDialog({ open, title, description, confirmLabel = 'Confirm', danger, onConfirm, onCancel }: ConfirmDialogProps) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-[90] grid place-items-center bg-black/50" onClick={onCancel}>
      <div
        className="bg-surface-1 border border-border rounded-lg p-6 max-w-sm w-full mx-4 animate-[fade-in_0.15s_ease-out_both]"
        onClick={(e) => e.stopPropagation()}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
      >
        <h3 id="confirm-title" className="text-sm font-medium text-text m-0 mb-2">{title}</h3>
        {description && <p className="text-[13px] text-text-secondary m-0 mb-4 leading-relaxed">{description}</p>}
        <div className="flex items-center justify-end gap-2 mt-4">
          <button
            onClick={onCancel}
            className="text-[12px] px-3 py-1.5 rounded-md border border-border bg-surface-2 text-text-secondary hover:text-text cursor-pointer transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            className={`text-[12px] px-3 py-1.5 rounded-md border cursor-pointer transition-colors ${
              danger
                ? 'border-danger/40 bg-danger/10 text-danger hover:bg-danger/20'
                : 'border-border bg-surface-2 text-text-secondary hover:text-text hover:border-text-muted'
            }`}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
