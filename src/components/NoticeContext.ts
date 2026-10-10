import { createContext } from 'react';
export const NoticeContext = createContext<{ text: string; error?: boolean } | null>(null);
