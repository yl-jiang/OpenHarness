import { useMemo, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkBreaks from 'remark-breaks';
import remarkGfm from 'remark-gfm';
import type { Components } from 'react-markdown';

interface MarkdownViewProps {
  content: string;
  headingIds?: string[];
}

export function MarkdownView({ content, headingIds }: MarkdownViewProps) {
  const counterRef = useRef(0);
  counterRef.current = 0;

  const components = useMemo<Components | undefined>(() => {
    if (!headingIds) return undefined;
    const readNextId = () => {
      const id = headingIds[counterRef.current] ?? `section-${counterRef.current}`;
      counterRef.current += 1;
      return id;
    };
    return {
      h1: ({ children, node: _node, ...rest }) => (
        <h1 id={readNextId()} className="scroll-mt-20" {...rest}>{children}</h1>
      ),
      h2: ({ children, node: _node, ...rest }) => (
        <h2 id={readNextId()} className="scroll-mt-20" {...rest}>{children}</h2>
      ),
      h3: ({ children, node: _node, ...rest }) => (
        <h3 id={readNextId()} className="scroll-mt-20" {...rest}>{children}</h3>
      ),
    } as Components;
  }, [headingIds]);

  return (
    <div className="prose-dark">
      <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]} components={components}>
        {content || '_No content._'}
      </ReactMarkdown>
    </div>
  );
}
