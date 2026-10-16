import { type ReactNode, useEffect } from "react";

interface ModalProps {
  title: ReactNode;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  wide?: boolean;
}

/** A centred dialog. Escape or a click on the backdrop closes it. */
export function Modal({ title, onClose, children, footer, wide = false }: ModalProps) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center p-4 overflow-y-auto bg-stone-900/50 sm:p-8"
      onMouseDown={(event) => event.target === event.currentTarget && onClose()}
    >
      <div
        role="dialog"
        aria-modal="true"
        className={`flex flex-col w-full ${wide ? "max-w-5xl" : "max-w-xl"} bg-white rounded-2xl shadow-xl`}
      >
        <header className="flex items-center gap-3 px-5 py-4 border-b border-stone-100">
          <h2 className="text-lg font-semibold">{title}</h2>
          <button
            type="button"
            aria-label="Close"
            className="shrink-0 ml-auto size-8 text-xl leading-none text-stone-400 rounded-full hover:bg-stone-100 hover:text-stone-700"
            onClick={onClose}
          >
            ×
          </button>
        </header>
        <div className="flex flex-col gap-4 p-5">{children}</div>
        {footer && <footer className="flex flex-wrap items-center gap-2 px-5 py-4 border-t border-stone-100">{footer}</footer>}
      </div>
    </div>
  );
}
