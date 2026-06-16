import { useState, useCallback, useRef, useEffect } from 'react';
import { api } from '../api/client';
import type { AppName, MemoryItem } from '../api/types';
import { useToast } from './ToastProvider';

interface MemoryCardProps {
  appName: AppName;
}

const MEMORY_TYPE_COLORS: Record<string, string> = {
  user: 'bg-blue-500/20 text-blue-400 border-blue-500/30',
  feedback: 'bg-orange-500/20 text-orange-400 border-orange-500/30',
  project: 'bg-green-500/20 text-green-400 border-green-500/30',
  reference: 'bg-gray-500/20 text-gray-400 border-gray-500/30',
};

export function MemoryCard({ appName }: MemoryCardProps) {
  const [open, setOpen] = useState(false);
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [loading, setLoading] = useState(false);
  const { toast } = useToast();

  const loadMemories = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.memories(appName);
      setMemories(data);
    } catch (err) {
      toast(`Failed to load memories: ${err}`, 'error');
    } finally {
      setLoading(false);
    }
  }, [appName, toast]);

  const handleToggle = useCallback(() => {
    const newOpen = !open;
    setOpen(newOpen);
    if (newOpen && memories.length === 0) {
      loadMemories();
    }
  }, [open, memories.length, loadMemories]);

  const handleDelete = useCallback(async (id: string) => {
    if (!confirm('Are you sure you want to delete this memory? This cannot be undone.')) return;
    try {
      await api.deleteMemory(appName, id);
      toast('Memory deleted', 'success');
      await loadMemories();
    } catch (err) {
      toast(`Failed to delete memory: ${err}`, 'error');
    }
  }, [appName, loadMemories, toast]);

  const handleToggleDisabled = useCallback(async (memory: MemoryItem) => {
    try {
      await api.updateMemory(appName, memory.id, { disabled: !memory.disabled });
      toast(`Memory ${memory.disabled ? 'enabled' : 'disabled'}`, 'success');
      await loadMemories();
    } catch (err) {
      toast(`Failed to update memory: ${err}`, 'error');
    }
  }, [appName, loadMemories, toast]);

  const activeCount = memories.filter((m) => !m.disabled).length;
  const disabledCount = memories.filter((m) => m.disabled).length;

  return (
    <section className="border border-border rounded-lg bg-surface-1 overflow-hidden">
      <button
        type="button"
        onClick={handleToggle}
        className="flex items-center justify-between w-full px-4 py-2.5 border-b border-border-subtle cursor-pointer bg-transparent text-left hover:bg-surface-2/50 transition-colors"
      >
        <h3 className="text-[13px] font-medium text-text m-0 flex items-center gap-2">
          <span className="text-text-muted text-[14px]">🧠</span>
          Memory
          {memories.length > 0 && (
            <span className="text-[11px] text-text-muted ml-2">
              ({activeCount} active{disabledCount > 0 ? `, ${disabledCount} disabled` : ''})
            </span>
          )}
        </h3>
        <span className="text-text-muted text-[12px] transition-transform" style={{ transform: open ? 'rotate(90deg)' : 'none' }}>
          ›
        </span>
      </button>

      {open && (
        <div className="px-4 py-3">
          {loading ? (
            <div className="text-[12px] text-text-muted py-8 text-center">Loading...</div>
          ) : memories.length === 0 ? (
            <div className="text-[12px] text-text-muted py-8 text-center">
              No memories yet. Use /memory command to create entries.
            </div>
          ) : (
            <div className="space-y-2 max-h-[400px] overflow-y-auto">
              {memories.map((memory) => (
                <MemoryRow
                  key={memory.id}
                  memory={memory}
                  onDelete={() => handleDelete(memory.id)}
                  onToggleDisabled={() => handleToggleDisabled(memory)}
                />
              ))}
            </div>
          )}
        </div>
      )}
    </section>
  );
}

function MemoryRow({ memory, onDelete, onToggleDisabled }: { memory: MemoryItem; onDelete: () => void; onToggleDisabled: () => void }) {
  const [expanded, setExpanded] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const typeColor = MEMORY_TYPE_COLORS[memory.type] || MEMORY_TYPE_COLORS.reference;

  useEffect(() => {
    if (!menuOpen) return;
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [menuOpen]);

  return (
    <div
      className={`group p-3 rounded border border-border-subtle bg-surface-2 transition-all ${
        memory.disabled ? 'opacity-50 italic' : ''
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0 cursor-pointer" onClick={() => setExpanded(!expanded)}>
          <div className="flex items-center gap-2 mb-1">
            <span className="text-text-muted text-[11px] transition-transform" style={{ transform: expanded ? 'rotate(90deg)' : 'none' }}>
              ›
            </span>
            <h4 className={`text-[13px] font-medium text-text truncate ${memory.disabled ? 'line-through' : ''}`}>
              {memory.name}
            </h4>
            <span className={`text-[10px] px-1.5 py-0.5 rounded border ${typeColor}`}>
              {memory.type}
            </span>
            {memory.importance > 3 && (
              <span className="text-[10px] text-yellow-400">★</span>
            )}
          </div>
          {memory.description && (
            <p className={`text-[11px] text-text-secondary mb-1.5 ${expanded ? '' : 'line-clamp-2'}`}>
              {memory.description}
            </p>
          )}
          {expanded && memory.content && (
            <div className="mt-2 pt-2 border-t border-border-subtle">
              <div className="text-[11px] text-text-secondary whitespace-pre-wrap font-mono bg-surface-3/50 p-2 rounded max-h-[300px] overflow-y-auto">
                {memory.content}
              </div>
            </div>
          )}
          <div className="flex items-center gap-3 text-[10px] text-text-muted mt-1.5">
            <span>Updated: {formatDate(memory.updated_at)}</span>
            {memory.tags.length > 0 && (
              <span className="flex gap-1">
                {memory.tags.slice(0, 3).map((tag) => (
                  <span key={tag} className="text-accent-solo">#{tag}</span>
                ))}
                {memory.tags.length > 3 && <span>+{memory.tags.length - 3}</span>}
              </span>
            )}
          </div>
        </div>
        <div ref={menuRef} className="relative">
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); setMenuOpen(!menuOpen); }}
            className="p-1 rounded text-text-muted hover:text-text hover:bg-surface-3 transition-colors opacity-0 group-hover:opacity-100"
            title="Actions"
          >
            <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
              <circle cx="8" cy="3" r="1.5" />
              <circle cx="8" cy="8" r="1.5" />
              <circle cx="8" cy="13" r="1.5" />
            </svg>
          </button>
          {menuOpen && (
            <div className="absolute right-0 top-full mt-1 z-10 min-w-[110px] bg-surface-2 border border-border rounded-md shadow-lg py-1">
              <button
                type="button"
                onClick={(e) => { e.stopPropagation(); setMenuOpen(false); onToggleDisabled(); }}
                className="w-full text-left px-3 py-1.5 text-sm text-text hover:bg-surface-3 transition-colors"
              >
                {memory.disabled ? 'Enable' : 'Disable'}
              </button>
              <div className="border-t border-border-subtle my-1" />
              <button
                type="button"
                onClick={(e) => { e.stopPropagation(); setMenuOpen(false); onDelete(); }}
                className="w-full text-left px-3 py-1.5 text-sm text-red-400 hover:bg-red-500/10 transition-colors"
              >
                Delete
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function formatDate(isoString: string): string {
  try {
    const date = new Date(isoString);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

    if (diffDays === 0) return 'Today';
    if (diffDays === 1) return 'Yesterday';
    if (diffDays < 7) return `${diffDays} days ago`;
    if (diffDays < 30) return `${Math.floor(diffDays / 7)} weeks ago`;
    
    return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
  } catch {
    return isoString;
  }
}
