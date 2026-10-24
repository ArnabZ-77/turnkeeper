import { useEffect } from "react";
import type { Toast } from "../state";

export interface ToastsProps {
  toasts: Toast[];
  onDismiss: (id: number) => void;
  /** Auto-dismiss delay in ms (errors stay twice as long). */
  ttlMs?: number;
}

function ToastItem({ toast, onDismiss, ttlMs }: { toast: Toast; onDismiss: (id: number) => void; ttlMs: number }) {
  useEffect(() => {
    const id = setTimeout(() => onDismiss(toast.id), toast.level === "error" ? ttlMs * 2 : ttlMs);
    return () => clearTimeout(id);
  }, [toast.id, toast.level, onDismiss, ttlMs]);
  return (
    <div className={`tk-toast tk-toast-${toast.level}`} role={toast.level === "error" ? "alert" : "status"}>
      <span>{toast.text}</span>
      <button type="button" onClick={() => onDismiss(toast.id)} aria-label="Dismiss">
        ×
      </button>
    </div>
  );
}

export function Toasts({ toasts, onDismiss, ttlMs = 4000 }: ToastsProps) {
  if (!toasts.length) return null;
  return (
    <div className="tk-toasts" aria-live="polite">
      {toasts.map((t) => (
        <ToastItem key={t.id} toast={t} onDismiss={onDismiss} ttlMs={ttlMs} />
      ))}
    </div>
  );
}
