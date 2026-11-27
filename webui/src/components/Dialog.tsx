import { type ReactNode, useEffect, useId, useLayoutEffect, useRef } from "react";

interface Props {
  title: ReactNode;
  children: ReactNode;
  /** Esc (and the browser's own close request) calls this; the screen decides whether the dialog goes away. */
  onCancel: () => void;
  /** While something runs, Esc does nothing (the request is not abandoned half way). */
  busy?: boolean;
  className?: string;
  describedBy?: string;
}

/**
 * A modal dialog on the native <dialog> element: showModal() keeps the focus inside, makes the page behind inert and
 * draws a backdrop. The first element with `data-autofocus` gets the focus (the safe button of a destructive dialog);
 * otherwise the browser picks the first focusable element. It is open while it is rendered.
 */
export function Dialog({ title, children, onCancel, busy, className, describedBy }: Props) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const before = useRef<Element | null>(null);
  const cancelRef = useRef(onCancel);
  const busyRef = useRef(busy);
  useEffect(() => {
    cancelRef.current = onCancel;
    busyRef.current = busy;
  });

  useLayoutEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    before.current = document.activeElement;
    if (!dialog.open) {
      if (typeof dialog.showModal === "function") dialog.showModal();
      else dialog.setAttribute("open", "");
    }
    const first = dialog.querySelector<HTMLElement>("[data-autofocus]");
    first?.focus();
    return () => {
      if (dialog.open) {
        if (typeof dialog.close === "function") dialog.close();
        else dialog.removeAttribute("open");
      }
      const back = before.current;
      if (back instanceof HTMLElement && back.isConnected) back.focus();
    };
  }, []);

  function requestCancel() {
    if (!busyRef.current) cancelRef.current();
  }

  return (
    <dialog
      ref={ref}
      className={`dialog${className ? ` ${className}` : ""}`}
      aria-modal="true"
      aria-labelledby={titleId}
      aria-describedby={describedBy}
      onCancel={(e) => {
        // The browser closes the dialog by itself on Esc; the screen decides instead (it unmounts the dialog).
        e.preventDefault();
        requestCancel();
      }}
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.preventDefault();
          e.stopPropagation();
          requestCancel();
        }
      }}
    >
      <h2 id={titleId}>{title}</h2>
      {children}
    </dialog>
  );
}
