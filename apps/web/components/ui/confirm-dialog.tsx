'use client';

import * as React from 'react';
import { useTranslations } from 'next-intl';
import { toast } from 'sonner';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
  DialogDescription,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';

export interface ConfirmDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: string;
  /** Label for the confirm (action) button. Defaults to the localized common label. */
  confirmLabel?: string;
  /** Label for the dismiss button. Defaults to the localized common label. */
  cancelLabel?: string;
  /**
   * When true the confirm button uses the red `destructive` variant instead of
   * the blue `primary` one. Geist's colour-means-meaning rule: irreversible /
   * destructive actions must read as red, never blue.
   */
  destructive?: boolean;
  /** Async or sync action run when the user confirms. Dialog closes on success. */
  onConfirm: () => void | Promise<void>;
  /**
   * Toast shown after `onConfirm` resolves. Defaults to a generic success
   * message; pass a translated string to be specific (e.g. "Member removed").
   */
  successMessage?: string;
  /**
   * Toast shown if `onConfirm` rejects. Defaults to a generic failure message.
   * The dialog stays open and renders the same error in its action area.
   */
  errorMessage?: string;
}

/**
 * Geist-styled confirmation modal — a drop-in replacement for the browser's
 * native `window.confirm`.
 *
 * It is built on the same Radix `Dialog` as every other modal in the app, so it
 * inherits the same compact panel, focus, and action treatment as every other
 * dialog. The cancel (safe) button precedes the destructive action.
 */
export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel,
  cancelLabel,
  destructive = false,
  onConfirm,
  successMessage,
  errorMessage,
}: ConfirmDialogProps) {
  const t = useTranslations('common');
  const [busy, setBusy] = React.useState(false);
  const [actionError, setActionError] = React.useState<string | null>(null);
  const cancelButtonRef = React.useRef<HTMLButtonElement>(null);

  React.useEffect(() => {
    if (!open) setActionError(null);
  }, [open]);

  async function handleConfirm() {
    if (busy) return;
    setBusy(true);
    setActionError(null);
    try {
      await onConfirm();
      // Close only after the backend-backed action settles successfully.
      onOpenChange(false);
      toast.success(successMessage ?? t('actionSuccess'));
    } catch {
      const message = errorMessage ?? t('actionFailed');
      setActionError(message);
      toast.error(message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(next) => !busy && onOpenChange(next)}>
      <DialogContent
        onOpenAutoFocus={(event) => {
          // A confirmation dialog must initially favor the safe action.
          event.preventDefault();
          cancelButtonRef.current?.focus();
        }}
      >
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          {description ? <DialogDescription>{description}</DialogDescription> : null}
        </DialogHeader>
        {actionError ? <p className="dashboard-feedback" role="alert">{actionError}</p> : null}
        <DialogFooter>
          <Button ref={cancelButtonRef} variant="outline" onClick={() => onOpenChange(false)} disabled={busy}>
            {cancelLabel ?? t('cancel')}
          </Button>
          <Button
            variant={destructive ? 'destructive' : 'primary'}
            onClick={handleConfirm}
            disabled={busy}
            loading={busy}
          >
            {confirmLabel ?? t('confirm')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
