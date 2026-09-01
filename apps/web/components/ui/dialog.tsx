'use client';

import * as React from 'react';
import * as DialogPrimitive from '@radix-ui/react-dialog';
import { useTranslations } from 'next-intl';
import { cn } from '@/lib/utils';
import { IconX } from '@/components/icons';

// Dashboard modal and responsive Sheet/Drawer surfaces built on Radix Dialog.
// Geist Sheets keep the surrounding desktop page available, while the mobile
// Drawer remains modal so its compact task stays focused.
const DrawerVariantContext = React.createContext<(isDrawer: boolean) => void>(() => undefined);
const SheetReturnFocusContext = React.createContext<React.MutableRefObject<HTMLElement | null> | null>(null);

const focusableSelector = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

function sheetFocusableElements(container: HTMLElement) {
  return Array.from(container.querySelectorAll<HTMLElement>(focusableSelector)).filter((element) => {
    const style = window.getComputedStyle(element);
    return style.visibility !== 'hidden' && style.display !== 'none';
  });
}

function useDesktopSheet() {
  const [isDesktop, setIsDesktop] = React.useState(false);

  React.useEffect(() => {
    const query = window.matchMedia('(min-width: 640px)');
    const sync = () => setIsDesktop(query.matches);
    sync();
    query.addEventListener('change', sync);
    return () => query.removeEventListener('change', sync);
  }, []);

  return isDesktop;
}

function Dialog({ modal, children, ...props }: React.ComponentPropsWithoutRef<typeof DialogPrimitive.Root>) {
  const [hasDrawer, setHasDrawer] = React.useState(false);
  const isDesktop = useDesktopSheet();
  const returnFocusRef = React.useRef<HTMLElement | null>(null);

  return <SheetReturnFocusContext.Provider value={returnFocusRef}>
    <DrawerVariantContext.Provider value={setHasDrawer}>
      <DialogPrimitive.Root modal={modal ?? (hasDrawer ? !isDesktop : true)} {...props}>
        {children}
      </DialogPrimitive.Root>
    </DrawerVariantContext.Provider>
  </SheetReturnFocusContext.Provider>;
}
const DialogTrigger = DialogPrimitive.Trigger;
const DialogClose = DialogPrimitive.Close;
const DialogPortal = DialogPrimitive.Portal;

const DialogOverlay = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Overlay>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Overlay>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Overlay
    ref={ref}
    className={cn(
      'fixed inset-0 z-50 bg-black/40 backdrop-blur-[1px] data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:duration-150',
      className,
    )}
    {...props}
  />
));
DialogOverlay.displayName = DialogPrimitive.Overlay.displayName;

const DialogContent = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Content> & {
    showClose?: boolean;
    variant?: 'dialog' | 'drawer';
  }
>(({ className, children, showClose = true, variant = 'dialog', onInteractOutside, onOpenAutoFocus, onCloseAutoFocus, onKeyDownCapture, ...props }, ref) => {
  const t = useTranslations('common');
  const setHasDrawer = React.useContext(DrawerVariantContext);
  const returnFocusRef = React.useContext(SheetReturnFocusContext);
  const isDesktop = useDesktopSheet();
  const isDesktopSheet = variant === 'drawer' && isDesktop;

  React.useEffect(() => {
    setHasDrawer(variant === 'drawer');
    return () => setHasDrawer(false);
  }, [setHasDrawer, variant]);

  return <DialogPortal>
    <DialogOverlay className={variant === 'drawer' ? 'dialog-drawer-overlay' : undefined} />
    <DialogPrimitive.Content
      ref={ref}
      data-variant={variant}
      onOpenAutoFocus={(event) => {
        if (isDesktopSheet && returnFocusRef) {
          returnFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
        }
        onOpenAutoFocus?.(event);
      }}
      onCloseAutoFocus={(event) => {
        onCloseAutoFocus?.(event);
        if (!isDesktopSheet || event.defaultPrevented) return;
        event.preventDefault();
        returnFocusRef?.current?.focus({ preventScroll: true });
      }}
      onKeyDownCapture={(event) => {
        onKeyDownCapture?.(event);
        if (!isDesktopSheet || event.defaultPrevented || event.key !== 'Tab') return;

        const focusable = sheetFocusableElements(event.currentTarget);
        if (!focusable.length) {
          event.preventDefault();
          return;
        }

        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        const activeElement = document.activeElement;
        if (event.shiftKey && (activeElement === first || !event.currentTarget.contains(activeElement))) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }}
      onInteractOutside={variant === 'drawer' && isDesktop
        ? (event) => event.preventDefault()
        : onInteractOutside}
      className={cn(
        'dialog-surface fixed z-50 border border-border bg-card text-card-foreground shadow-elevation-5 focus:outline-none data-[state=open]:animate-in data-[state=closed]:animate-out',
        variant !== 'drawer' && 'focus-visible:shadow-geist-focus',
        variant === 'drawer'
          ? 'inset-x-0 bottom-0 flex max-h-[calc(100dvh-48px)] w-full max-w-none flex-col gap-4 overflow-y-auto rounded-t-lg border-x-0 border-b-0 p-6 data-[state=open]:slide-in-from-bottom data-[state=open]:duration-200 data-[state=closed]:slide-out-to-bottom data-[state=closed]:duration-150 sm:inset-y-0 sm:left-auto sm:right-0 sm:h-dvh sm:max-h-none sm:w-[min(600px,calc(100vw-48px))] sm:rounded-none sm:border-y-0 sm:border-r-0 sm:border-l sm:data-[state=open]:[--tw-enter-translate-y:0%] sm:data-[state=open]:slide-in-from-right sm:data-[state=closed]:[--tw-exit-translate-y:0%] sm:data-[state=closed]:slide-out-to-right'
          : 'left-1/2 top-1/2 grid w-full max-w-lg -translate-x-1/2 -translate-y-1/2 gap-4 rounded-lg p-6 data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95 data-[state=open]:duration-200 data-[state=open]:[--tw-enter-translate-x:-50%] data-[state=open]:[--tw-enter-translate-y:-50%] data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95 data-[state=closed]:duration-150 data-[state=closed]:[--tw-exit-translate-x:-50%] data-[state=closed]:[--tw-exit-translate-y:-50%]',
        className,
      )}
      {...props}
    >
      {children}
      {showClose && (
        <DialogPrimitive.Close
          className="absolute right-4 top-4 inline-flex size-8 items-center justify-center rounded-[6px] text-muted-foreground opacity-70 transition hover:bg-[var(--dashboard-hover)] hover:opacity-100 focus:outline-none focus-visible:shadow-geist-focus"
          aria-label={t('close')}
        >
          <IconX size={16} />
        </DialogPrimitive.Close>
      )}
    </DialogPrimitive.Content>
  </DialogPortal>;
});
DialogContent.displayName = DialogPrimitive.Content.displayName;

function DialogHeader({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div data-dialog-header className={cn('flex flex-col gap-1.5 pr-8 text-left', className)} {...props} />;
}

function DialogFooter({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div data-dialog-footer className={cn('mt-auto flex flex-wrap justify-end gap-2 pt-2', className)} {...props} />;
}

const DialogTitle = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Title>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Title>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Title
    ref={ref}
    className={cn('text-[16px] font-semibold leading-none tracking-normal', className)}
    {...props}
  />
));
DialogTitle.displayName = DialogPrimitive.Title.displayName;

const DialogDescription = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Description>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Description>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Description
    ref={ref}
    className={cn('text-sm text-muted-foreground', className)}
    {...props}
  />
));
DialogDescription.displayName = DialogPrimitive.Description.displayName;

export {
  Dialog,
  DialogTrigger,
  DialogClose,
  DialogPortal,
  DialogOverlay,
  DialogContent,
  DialogHeader,
  DialogFooter,
  DialogTitle,
  DialogDescription,
};
