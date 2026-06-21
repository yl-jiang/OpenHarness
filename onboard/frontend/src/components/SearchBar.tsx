import { FormEvent, useEffect, useRef, useState } from 'react';

interface SearchBarProps {
  initialValue?: string;
  onSearch: (value: string) => void;
  /** Fires on every keystroke with the current raw value. */
  onChange?: (value: string) => void;
  /** When true, registers a global Cmd/Ctrl+K shortcut to focus this input */
  globalShortcut?: boolean;
  /** `compact` is the default toolbar style; `hero` is a large, centered landing-page search box. */
  variant?: 'compact' | 'hero';
  placeholder?: string;
}

export function SearchBar({
  initialValue = '',
  onSearch,
  onChange,
  globalShortcut,
  variant = 'compact',
  placeholder = 'Search… (use #tag to filter by tag)',
}: SearchBarProps) {
  const [value, setValue] = useState(initialValue);
  const inputRef = useRef<HTMLInputElement>(null);

  // Keep the input in sync when navigated to with a new query (e.g. ?q=...).
  useEffect(() => { setValue(initialValue); }, [initialValue]);

  useEffect(() => {
    if (!globalShortcut) return;
    function handleKeydown(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        inputRef.current?.focus();
        inputRef.current?.select();
      }
    }
    document.addEventListener('keydown', handleKeydown);
    return () => document.removeEventListener('keydown', handleKeydown);
  }, [globalShortcut]);

  // Tag-search mode: the FIRST character is '#'. While active, the entire
  // input (the '#' plus whatever tag value follows) is rendered in the
  // accent color so the user immediately sees they are filtering by tag.
  // Tokens are parsed by the consumer (e.g. Search page) — this component
  // intentionally holds a single, raw value so what you type is what you see.
  const tagMode = value.startsWith('#');
  const isEmpty = value.length === 0;

  function submit(event?: FormEvent) {
    event?.preventDefault();
    const trimmed = value.trim();
    onSearch(trimmed);
  }

  const isHero = variant === 'hero';

  return (
    <form
      className={isHero ? 'flex items-center justify-center w-full' : 'flex items-center'}
      onSubmit={submit}
    >
      <style>{`
        /* Override the global :focus-visible golden outline for the search input.
           Scoped to [data-search] so it doesn't leak to other inputs. */
        [data-search] input:focus-visible { outline: none !important; }
      `}</style>
      <div
        data-search
        className={
          isHero
            ? 'flex items-center gap-4 w-full max-w-[46rem] px-6 py-4 bg-surface-1 border border-border/70 rounded-full shadow-[0_1px_6px_rgba(32,33,36,0.18)] transition-all focus-within:shadow-[0_1px_12px_rgba(32,33,36,0.28)] focus-within:border-border'
            : 'flex items-center flex-wrap gap-1 min-w-0 w-72 px-2 py-1 bg-surface-2 border border-border rounded-md transition-colors focus-within:border-text-muted'
        }
      >
        <span
          className={
            isHero
              ? 'text-text-muted shrink-0 relative z-10 pointer-events-none'
              : 'text-text-muted text-xs shrink-0 relative z-10 pointer-events-none'
          }
        >
          <svg
            width={isHero ? 22 : 14}
            height={isHero ? 22 : 14}
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth={isHero ? 1.8 : 2}
          >
            <circle cx="11" cy="11" r="8" />
            <path d="M21 21l-4.35-4.35" />
          </svg>
        </span>
        <input
          ref={inputRef}
          value={value}
          onChange={(event) => {
            setValue(event.target.value);
            onChange?.(event.target.value);
          }}
          placeholder={placeholder}
          aria-label="Search"
          autoFocus={isHero}
          className={
            isHero
              ? 'relative flex-1 min-w-[160px] bg-transparent text-lg leading-[1.5rem] placeholder:text-text-muted/60 outline-none py-0'
              : 'relative flex-1 min-w-[80px] bg-transparent text-[13px] leading-[1.25rem] placeholder:text-text-muted outline-none py-0.5'
          }
          style={{
            color: tagMode ? 'var(--color-accent-solo)' : undefined,
            caretColor: 'var(--color-text)',
          }}
        />
        {isHero && isEmpty && (
          <kbd className="relative z-10 text-[11px] font-mono text-text-muted/50 pointer-events-none shrink-0">
            ↵
          </kbd>
        )}
        {globalShortcut && isEmpty && !isHero && (
          <kbd className="relative z-10 text-[10px] font-mono text-text-muted/60 pointer-events-none hidden sm:inline">
            ⌘K
          </kbd>
        )}
      </div>
    </form>
  );
}
