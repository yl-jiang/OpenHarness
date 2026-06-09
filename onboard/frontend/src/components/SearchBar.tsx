import { FormEvent, useEffect, useRef, useState } from 'react';

interface SearchBarProps {
  initialValue?: string;
  onSearch: (value: string) => void;
  /** When true, registers a global Cmd/Ctrl+K shortcut to focus this input */
  globalShortcut?: boolean;
}

export function SearchBar({ initialValue = '', onSearch, globalShortcut }: SearchBarProps) {
  const [value, setValue] = useState(initialValue);
  const inputRef = useRef<HTMLInputElement>(null);

  // Sync initialValue when it changes externally (e.g. URL query param)
  useEffect(() => { setValue(initialValue); }, [initialValue]);

  // Global Cmd/Ctrl+K shortcut
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

  function submit(event: FormEvent) {
    event.preventDefault();
    onSearch(value.trim());
  }

  return (
    <form className="flex items-center" onSubmit={submit}>
      <div className="relative">
        <span className="absolute left-2.5 top-1/2 -translate-y-1/2 text-text-muted text-xs">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/></svg>
        </span>
        <input
          ref={inputRef}
          value={value}
          onChange={(event) => setValue(event.target.value)}
          placeholder="Search..."
          aria-label="Search"
          className="w-52 pl-8 pr-3 py-1.5 text-[13px] bg-surface-2 border border-border rounded-md text-text placeholder:text-text-muted outline-none focus:border-text-muted transition-colors"
        />
        {globalShortcut && (
          <kbd className="absolute right-2 top-1/2 -translate-y-1/2 text-[10px] font-mono text-text-muted/60 pointer-events-none hidden sm:inline">
            ⌘K
          </kbd>
        )}
      </div>
    </form>
  );
}
