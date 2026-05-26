import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

interface MarkdownViewProps {
  content: string;
}

export function MarkdownView({ content }: MarkdownViewProps) {
  return (
    <div className="prose-dark">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{content || '_No content._'}</ReactMarkdown>
    </div>
  );
}
