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
    <form className="search-bar" onSubmit={submit}>
      <input
        value={value}
        onChange={(event) => setValue(event.target.value)}
        placeholder="Search records, tags, reports..."
      />
      <button type="submit">Search</button>
    </form>
  );
}
