import { FormEvent, useState } from 'react';

interface SearchBarProps {
  initialValue?: string;
  onSearch: (value: string) => void;
}

export function SearchBar({ initialValue = '', onSearch }: SearchBarProps) {
  const [value, setValue] = useState(initialValue);

  function submit(event: FormEvent) {
    event.preventDefault();
    onSearch(value.trim());
  }

  return (
    <form className="flex items-center" onSubmit={submit}>
      <div className="relative">
        <span className="absolute left-2.5 top-1/2 -translate-y-1/2 text-text-muted text-xs">⌕</span>
        <input
          value={value}
          onChange={(event) => setValue(event.target.value)}
          placeholder="Search..."
          className="w-52 pl-7 pr-3 py-1.5 text-[13px] bg-surface-2 border border-border rounded-md text-text placeholder:text-text-muted outline-none focus:border-text-muted transition-colors"
        />
      </div>
    </form>
  );
}
