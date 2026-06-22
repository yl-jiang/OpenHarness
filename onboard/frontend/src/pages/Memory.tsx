import { useState, useEffect, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import { api } from '../api/client';
import type { AppName, MemoryItem, MemoryType } from '../api/types';
import { useToast } from '../components/ToastProvider';
import { ConfirmDialog } from '../components/ConfirmDialog';

const MEMORY_TYPE_COLORS: Record<string, string> = {
  user: 'bg-blue-500/20 text-blue-400 border-blue-500/30',
  feedback: 'bg-orange-500/20 text-orange-400 border-orange-500/30',
  project: 'bg-green-500/20 text-green-400 border-green-500/30',
  reference: 'bg-gray-500/20 text-gray-400 border-gray-500/30',
};

export function Memory({ appName }: { appName: AppName }) {
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [filterType, setFilterType] = useState<string>('all');
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const { toast } = useToast();

  useEffect(() => {
    loadMemories();
  }, [appName]);

  const loadMemories = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.memories(appName);
      setMemories(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load memories');
      toast('Failed to load memories', 'error');
    } finally {
      setLoading(false);
    }
  };

  const handleDelete = async (id: string) => {
    setPendingDeleteId(id);
  };

  const confirmDelete = async () => {
    if (!pendingDeleteId) return;
    setDeleting(true);
    try {
      await api.deleteMemory(appName, pendingDeleteId);
      toast('Memory deleted successfully', 'success');
      loadMemories();
    } catch (err) {
      toast(err instanceof Error ? err.message : 'Failed to delete memory', 'error');
    } finally {
      setDeleting(false);
      setPendingDeleteId(null);
    }
  };

  const handleToggleDisabled = async (memory: MemoryItem) => {
    try {
      await api.updateMemory(appName, memory.id, { disabled: !memory.disabled });
      toast(`Memory ${memory.disabled ? 'enabled' : 'disabled'}`, 'success');
      loadMemories();
    } catch (err) {
      toast(err instanceof Error ? err.message : 'Failed to update memory', 'error');
    }
  };

  const handleUpdate = (updatedMemory: MemoryItem) => {
    toast('Memory updated successfully', 'success');
    loadMemories();
  };

  const filteredMemories = memories.filter((memory) => {
    const matchesSearch = searchQuery === '' || 
      memory.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      memory.description.toLowerCase().includes(searchQuery.toLowerCase()) ||
      memory.content.toLowerCase().includes(searchQuery.toLowerCase()) ||
      memory.tags.some(tag => tag.toLowerCase().includes(searchQuery.toLowerCase()));
    
    const matchesType = filterType === 'all' || memory.type === filterType;
    
    return matchesSearch && matchesType;
  });

  const activeMemories = filteredMemories.filter(m => !m.disabled);
  const disabledMemories = filteredMemories.filter(m => m.disabled);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-text">Memory</h1>
          <p className="text-sm text-text-muted mt-1">
            Manage your memories and context
          </p>
        </div>
        <button
          onClick={loadMemories}
          className="px-4 py-2 bg-surface-2 hover:bg-surface-3 text-text rounded-md transition-colors"
        >
          Refresh
        </button>
      </div>

      {/* Filters */}
      <div className="flex gap-4 items-center">
        <input
          type="text"
          placeholder="Search memories..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          className="flex-1 px-4 py-2 bg-surface-2 border border-border rounded-md text-text placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-accent-solo"
        />
        <select
          value={filterType}
          onChange={(e) => setFilterType(e.target.value)}
          className="px-4 py-2 bg-surface-2 border border-border rounded-md text-text focus:outline-none focus:ring-2 focus:ring-accent-solo"
        >
          <option value="all">All Types</option>
          <option value="user">User</option>
          <option value="project">Project</option>
          <option value="feedback">Feedback</option>
          <option value="reference">Reference</option>
        </select>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-3 gap-4">
        <div className="bg-surface-1 border border-border rounded-lg p-4">
          <div className="text-2xl font-bold text-text">{filteredMemories.length}</div>
          <div className="text-sm text-text-muted">Total</div>
        </div>
        <div className="bg-surface-1 border border-border rounded-lg p-4">
          <div className="text-2xl font-bold text-green-400">{activeMemories.length}</div>
          <div className="text-sm text-text-muted">Active</div>
        </div>
        <div className="bg-surface-1 border border-border rounded-lg p-4">
          <div className="text-2xl font-bold text-orange-400">{disabledMemories.length}</div>
          <div className="text-sm text-text-muted">Disabled</div>
        </div>
      </div>

      {/* Memory List */}
      {loading ? (
        <div className="flex items-center justify-center py-12">
          <div className="text-text-muted">Loading memories...</div>
        </div>
      ) : error ? (
        <div className="flex items-center justify-center py-12">
          <div className="text-red-400">{error}</div>
        </div>
      ) : filteredMemories.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-12 text-center">
          <div className="text-4xl mb-4">🧠</div>
          <div className="text-text-muted">
            {searchQuery || filterType !== 'all' 
              ? 'No memories match your filters' 
              : 'No memories yet. Use the /memory command to create your first memory.'}
          </div>
        </div>
      ) : (
        <div className="space-y-3">
          {filteredMemories.map((memory) => (
            <MemoryItem
              key={memory.id}
              appName={appName}
              memory={memory}
              onDelete={handleDelete}
              onToggleDisabled={handleToggleDisabled}
              onUpdate={handleUpdate}
            />
          ))}
        </div>
      )}
      <ConfirmDialog
        open={!!pendingDeleteId}
        title="Delete Memory"
        description="This memory will be permanently deleted. This action cannot be undone."
        confirmLabel="Delete"
        danger
        loading={deleting}
        onConfirm={confirmDelete}
        onCancel={() => setPendingDeleteId(null)}
      />
    </div>
  );
}

function ActionMenu({
  memory,
  onEdit,
  onToggleDisabled,
  onDelete,
}: {
  memory: MemoryItem;
  onEdit: () => void;
  onToggleDisabled: () => void;
  onDelete: () => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={(e) => { e.stopPropagation(); setOpen(!open); }}
        className="p-1 rounded text-text-muted hover:text-text hover:bg-surface-3 transition-colors opacity-0 group-hover:opacity-100"
        title="Actions"
      >
        <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
          <circle cx="8" cy="3" r="1.5" />
          <circle cx="8" cy="8" r="1.5" />
          <circle cx="8" cy="13" r="1.5" />
        </svg>
      </button>
      {open && (
        <div className="absolute right-0 top-full mt-1 z-10 min-w-[120px] bg-surface-2 border border-border rounded-md shadow-lg py-1">
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); setOpen(false); onEdit(); }}
            className="w-full text-left px-3 py-1.5 text-sm text-text hover:bg-surface-3 transition-colors"
          >
            Edit
          </button>
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); setOpen(false); onToggleDisabled(); }}
            className="w-full text-left px-3 py-1.5 text-sm text-text hover:bg-surface-3 transition-colors"
          >
            {memory.disabled ? 'Enable' : 'Disable'}
          </button>
          <div className="border-t border-border-subtle my-1" />
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); setOpen(false); onDelete(); }}
            className="w-full text-left px-3 py-1.5 text-sm text-red-400 hover:bg-red-500/10 transition-colors"
          >
            Delete
          </button>
        </div>
      )}
    </div>
  );
}

function MemoryItem({
  appName,
  memory,
  onDelete,
  onToggleDisabled,
  onUpdate,
}: {
  appName: AppName;
  memory: MemoryItem;
  onDelete: (id: string) => void;
  onToggleDisabled: (memory: MemoryItem) => void;
  onUpdate: (memory: MemoryItem) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editData, setEditData] = useState<Partial<MemoryItem>>({});
  const typeColor = MEMORY_TYPE_COLORS[memory.type] || MEMORY_TYPE_COLORS.user;

  const handleEdit = () => {
    setEditData({
      name: memory.name,
      description: memory.description,
      content: memory.content,
      type: memory.type,
      tags: memory.tags,
    });
    setEditing(true);
    setExpanded(true);
  };

  const handleSave = async () => {
    try {
      await api.updateMemory(appName, memory.id, editData);
      onUpdate(memory);
      setEditing(false);
    } catch (err) {
      console.error('Failed to update memory:', err);
    }
  };

  const handleCancel = () => {
    setEditing(false);
    setEditData({});
  };

  return (
    <div
      className={`group bg-surface-1 border border-border rounded-lg transition-all ${
        memory.disabled ? 'opacity-60' : ''
      }`}
    >
      <div
        className="p-4 cursor-pointer hover:bg-surface-2 transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex items-start gap-3">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-2">
              <span className="text-text-muted text-[11px] transition-transform" style={{ transform: expanded ? 'rotate(90deg)' : 'none' }}>
                ›
              </span>
              <h3 className="text-base font-medium text-text truncate">
                {memory.name}
              </h3>
              <span className={`text-[10px] px-2 py-0.5 rounded border ${typeColor}`}>
                {memory.type}
              </span>
              {memory.importance > 3 && (
                <span className="text-yellow-400">★</span>
              )}
              {memory.disabled && (
                <span className="text-[10px] px-2 py-0.5 rounded bg-red-500/20 text-red-400 border border-red-500/30">
                  Disabled
                </span>
              )}
            </div>
            {memory.description && (
              <p className={`text-sm text-text-secondary mb-2 ${expanded ? '' : 'line-clamp-2'}`}>
                {memory.description}
              </p>
            )}
            <div className="flex items-center gap-3 text-xs text-text-muted">
              <span>Updated: {formatDate(memory.updated_at)}</span>
              {memory.tags.length > 0 && (
                <div className="flex gap-1">
                  {memory.tags.map((tag) => (
                    <span key={tag} className="px-2 py-0.5 bg-surface-2 rounded text-text-secondary">
                      #{tag}
                    </span>
                  ))}
                </div>
              )}
            </div>
          </div>
          {!editing && (
            <ActionMenu
              memory={memory}
              onEdit={handleEdit}
              onToggleDisabled={() => onToggleDisabled(memory)}
              onDelete={() => onDelete(memory.id)}
            />
          )}
        </div>
      </div>

      {expanded && (
        <div className="px-4 pb-4 pt-2 border-t border-border">
          {editing ? (
            <div className="space-y-3">
              <div>
                <label className="block text-sm text-text-muted mb-1">Name</label>
                <input
                  type="text"
                  value={editData.name || ''}
                  onChange={(e) => setEditData({ ...editData, name: e.target.value })}
                  className="w-full px-3 py-2 bg-surface-3/50 border border-border rounded text-text focus:outline-none focus:ring-2 focus:ring-accent-solo"
                />
              </div>
              <div>
                <label className="block text-sm text-text-muted mb-1">Description</label>
                <input
                  type="text"
                  value={editData.description || ''}
                  onChange={(e) => setEditData({ ...editData, description: e.target.value })}
                  className="w-full px-3 py-2 bg-surface-3/50 border border-border rounded text-text focus:outline-none focus:ring-2 focus:ring-accent-solo"
                />
              </div>
              <div>
                <label className="block text-sm text-text-muted mb-1">Type</label>
                <select
                  value={editData.type || 'user'}
                  onChange={(e) => setEditData({ ...editData, type: e.target.value as MemoryType })}
                  className="px-3 py-2 bg-surface-3/50 border border-border rounded text-text focus:outline-none focus:ring-2 focus:ring-accent-solo"
                >
                  <option value="user">User</option>
                  <option value="project">Project</option>
                  <option value="feedback">Feedback</option>
                  <option value="reference">Reference</option>
                </select>
              </div>
              <div>
                <label className="block text-sm text-text-muted mb-1">Tags (comma-separated)</label>
                <input
                  type="text"
                  value={(editData.tags || []).join(', ')}
                  onChange={(e) => setEditData({ ...editData, tags: e.target.value.split(',').map(t => t.trim()).filter(t => t) })}
                  className="w-full px-3 py-2 bg-surface-3/50 border border-border rounded text-text focus:outline-none focus:ring-2 focus:ring-accent-solo"
                />
              </div>
              <div>
                <label className="block text-sm text-text-muted mb-1">Content (Markdown)</label>
                <textarea
                  value={editData.content || ''}
                  onChange={(e) => setEditData({ ...editData, content: e.target.value })}
                  rows={8}
                  className="w-full px-3 py-2 bg-surface-3/50 border border-border rounded text-text font-mono text-sm focus:outline-none focus:ring-2 focus:ring-accent-solo resize-y"
                />
              </div>
              <div className="flex gap-2">
                <button
                  onClick={handleSave}
                  className="px-4 py-2 bg-accent-solo text-white rounded hover:bg-accent-solo/90 transition-colors"
                >
                  Save
                </button>
                <button
                  onClick={handleCancel}
                  className="px-4 py-2 bg-surface-3 text-text rounded hover:bg-surface-4 transition-colors"
                >
                  Cancel
                </button>
              </div>
            </div>
          ) : memory.content ? (
            <div className="prose prose-invert prose-sm max-w-none bg-surface-3/50 p-3 rounded max-h-[400px] overflow-y-auto">
              <ReactMarkdown>{memory.content}</ReactMarkdown>
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}

function formatDate(isoString: string): string {
  const date = new Date(isoString);
  return date.toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}
