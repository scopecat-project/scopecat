import { useId, useState } from "react";
import type { LaunchCatalogEntry } from "./launch-api";
import { secondaryButton } from "../../ui/styles";

type Entry = LaunchCatalogEntry;

export function ExperimentPicker({
  entries,
  selectedId,
  disabled,
  onSelect,
}: {
  entries: Entry[];
  selectedId: string;
  disabled: boolean;
  onSelect: (entry: Entry) => void;
}) {
  const [browsing, setBrowsing] = useState(false);
  const [query, setQuery] = useState("");
  const browserId = useId();
  const selected = entries.find((entry) => entry.id === selectedId);
  const terms = query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  const matches = entries.filter((entry) => {
    const text = `${entry.title} ${entry.id} ${entry.description}`.toLocaleLowerCase();
    return terms.every((term) => text.includes(term));
  });
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end gap-3">
        <label className="min-w-0 flex-1">
          Experiment
          <select
            aria-label="Experiment"
            disabled={disabled}
            value={selectedId}
            onChange={(event) => {
              const entry = entries.find((item) => item.id === event.target.value);
              if (entry) onSelect(entry);
            }}
            className="mt-1 block w-full min-w-0 border rounded p-2"
          >
            {!selected && <option value={selectedId}>{selectedId} (unavailable)</option>}
            {entries.map((entry) => (
              <option key={entry.id} value={entry.id}>
                {entry.title}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          className={secondaryButton}
          disabled={disabled}
          aria-expanded={browsing}
          aria-controls={browserId}
          onClick={() => setBrowsing(!browsing)}
        >
          {browsing ? "Close experiment browser" : "Browse experiments"}
        </button>
      </div>
      {browsing && (
        <section
          id={browserId}
          aria-label="Browse experiments"
          className="rounded border border-line p-3 space-y-3"
        >
          <label className="block">
            Search experiments
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Name, description or ID"
              className="mt-1 block w-full border rounded p-2"
            />
          </label>
          <p role="status" className="text-sm text-text-dim">
            {matches.length} of {entries.length} experiments. Browsing keeps your current
            preparation.
          </p>
          {matches.length === 0 && <p>No matching experiments. Try another name or description.</p>}
          <ul className="grid gap-3 md:grid-cols-2 max-h-96 overflow-y-auto">
            {matches.map((entry) => (
              <li key={entry.id} className="rounded border border-line p-3 space-y-2">
                <h4 className="font-semibold">{entry.title}</h4>
                <p className="text-sm text-text-dim whitespace-pre-line">{entry.description}</p>
                <p className="text-xs text-text-dim break-all">{entry.id}</p>
                <button
                  type="button"
                  className={secondaryButton}
                  disabled={disabled || entry.id === selectedId}
                  aria-label={`Prepare ${entry.title}`}
                  onClick={() => onSelect(entry)}
                >
                  {entry.id === selectedId ? "Selected" : "Prepare this experiment"}
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
