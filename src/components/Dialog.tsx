import { useContext, useEffect, useRef, type ReactNode } from 'react';
import { NoticeContext } from './NoticeContext';
import { X } from 'lucide-react';
export default function Dialog({ title, children, onClose, wide = false }: { title: string; children: ReactNode; onClose: () => void; wide?: boolean }) {
  const ref = useRef<HTMLDialogElement>(null);
  const notice = useContext(NoticeContext);
  useEffect(() => { ref.current?.showModal(); }, []);
  return <dialog ref={ref} className={wide ? 'dialog wide' : 'dialog'} onCancel={e => { e.preventDefault(); onClose(); }} onClick={e => { if (e.target === ref.current) onClose(); }}>
    <header><h2>{title}</h2><button className="icon-button" onClick={onClose} aria-label="Close dialog"><X size={20} /></button></header>{children}
    {notice ? <p className={`dialog-notice ${notice.error ? 'error' : ''}`} role={notice.error ? 'alert' : 'status'}>{notice.text}</p> : null}
  </dialog>;
}
