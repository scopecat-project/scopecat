import { ActionRow, Field, Select, Button, Input, Notice } from "../../ui/Controls";
import { useId, useState } from "react";
import type { LaunchCatalogEntry } from "./launch-api";

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
      <ActionRow>
        <Field className="min-w-0 flex-1 basis-64">
          实验类型
          <Select
            aria-label="实验类型"
            disabled={disabled}
            value={selectedId}
            onChange={(event) => {
              const entry = entries.find((item) => item.id === event.target.value);
              if (entry) onSelect(entry);
            }}
            className="mt-1 w-full min-w-0"
          >
            {!selected && <option value={selectedId}>{selectedId} (unavailable)</option>}
            {entries.map((entry) => (
              <option key={entry.id} value={entry.id}>
                {entry.title}
              </option>
            ))}
          </Select>
        </Field>
        <Button
          type="button"
          disabled={disabled}
          aria-expanded={browsing}
          aria-controls={browserId}
          onClick={() => setBrowsing(!browsing)}
        >
          {browsing ? "关闭实验浏览" : "浏览实验"}
        </Button>
      </ActionRow>
      {browsing && (
        <section
          id={browserId}
          aria-label="浏览实验"
          className="rounded border border-line p-3 space-y-3"
        >
          <Field>
            搜索实验
            <Input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Name, description or ID"
              className="mt-1 w-full"
            />
          </Field>
          <Notice role="status" className="text-sm text-text-dim">
            {matches.length} of {entries.length} experiments. Browsing keeps your current
            preparation.
          </Notice>
          {matches.length === 0 && <p>没有匹配的实验，请尝试其他名称或描述。</p>}
          <ul className="grid gap-3 md:grid-cols-2 max-h-96 overflow-y-auto">
            {matches.map((entry) => (
              <li key={entry.id} className="rounded border border-line p-3 space-y-2">
                <h4 className="font-semibold">{entry.title}</h4>
                <p className="text-sm text-text-dim whitespace-pre-line">{entry.description}</p>
                <p className="text-xs text-text-dim break-all">{entry.id}</p>
                <Button
                  type="button"
                  disabled={disabled || entry.id === selectedId}
                  aria-label={`Prepare ${entry.title}`}
                  onClick={() => onSelect(entry)}
                >
                  {entry.id === selectedId ? "已选择" : "准备此实验"}
                </Button>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
