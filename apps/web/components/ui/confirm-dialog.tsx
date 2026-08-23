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
  /** Label for the confirm (action) button. Defaults to "Confirm". */
  confirmLabel?: string;
  /** Label for the dismiss button. Defaults to "Cancel". */
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
   * The dialog stays open on error so any inline error UI remains visible too.
   */
  errorMessage?: string;
}

/**
 * Geist-styled confirmation modal — a drop-in replacement for the browser's
 * native `window.confirm`.
 *
 * It is built on the same Radix `Dialog` as every other modal in the app, so it
 * inherits the canonical Geist tokens: 12px overlay scale, 16px modal panel
 * radius, the 2px-gap + 4px-blue focus ring, and 600-weight headings with
 * negative tracking. The cancel (safe) button is the first focusable element,
 * so pressing Enter never triggers a destructive action by accident — matching
 * Geist's confirmation-dialog behaviour.
 */
export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  destructive = false,
  onConfirm,
  successMessage,
  errorMessage,
}: ConfirmDialogProps) {
  const t = useTranslations();
  const [busy, setBusy] = React.useState(false);

  async function handleConfirm() {
    if (busy) return;
    setBusy(true);
    try {
      await onConfirm();
      // Close only after the action settles successfully; on error the dialog
      // stays open so the caller's error message remains visible.
      onOpenChange(false);
      toast.success(successMessage ?? t('common.actionSuccess'));
    } catch {
      // Errors are owned by the caller's surrounding UI (inline message). A
      // toast makes the failure visible even while the modal is still open.
      toast.error(errorMessage ?? t('common.actionFailed'));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(next) => !busy && onOpenChange(next)}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          {description ? <DialogDescription>{description}</DialogDescription> : null}
        </DialogHeader>
        <DialogFooter>
          <Button variant="default" onClick={() => onOpenChange(false)} disabled={busy}>
            {cancelLabel}
          </Button>
          <Button
            variant={destructive ? 'destructive' : 'primary'}
            onClick={handleConfirm}
            disabled={busy}
          >
            {confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
