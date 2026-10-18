/**
 * Dynamically-loaded wrapper for the Monaco SQL editor.
 *
 * `monaco-editor` (and the ob-editor plugin/theme registration that runs at
 * module load of `./monaco-editor`) is a heavy dependency — loading it via
 * next/dynamic keeps it in a separate chunk that is fetched only when an
 * editor is actually rendered, instead of inside the main chat-page bundle.
 */
import dynamic from 'next/dynamic';
import type { OnChange } from '@monaco-editor/react';
import React from 'react';
import type { ISession } from './monaco-editor';

export type { ISession, OnChange };

interface MonacoEditorProps {
  className?: string;
  value: string;
  language: string;
  onChange?: OnChange;
  thoughts?: string;
  session?: ISession;
}

const MonacoEditorLoading = () => (
  <div className='w-full' style={{ minHeight: 200 }} aria-hidden />
);

const MonacoEditor = dynamic(
  () =>
    import(
        /* webpackChunkName: "monaco-editor-lazy" */ './monaco-editor'
      ) as Promise<{ default: React.ComponentType<MonacoEditorProps> }>,
  { ssr: false, loading: MonacoEditorLoading },
);

export default MonacoEditor;
