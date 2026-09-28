import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { components } from "../../api-schema";
import type { StoredParameterValue, ParameterEntity } from "../../api-contract";
import { apiClient, apiData, ApiError } from "../../api-client";
import { errorMessage } from "../../lib/presentation";
import { SetupPanel } from "./SetupPanel";
import { ParameterValueField } from "./ParameterValueField";
import { getSetupDefinitions } from "./setup-api";
import {
  commitParameterBranch,
  getParameterRevisions,
  saveParameterRevision,
  type ParameterRevision,
} from "./parameter-api";

export function ConfigWorkspace({
  daemonUnavailable,
  onSelectConfiguration,
}: {
  daemonUnavailable: boolean;
  onSelectConfiguration?: (choice: components["schemas"]["ConfigurationChoice-Input"]) => void;
}) {
  const cache = useQueryClient();
  const [operator, setOperator] = useState("local-operator");
  const [selected, setSelected] = useState("");
  const [editing, setEditing] = useState<ParameterRevision>();
  const versions = useQuery({
    queryKey: ["parameter-revisions"],
    queryFn: ({ signal }) => getParameterRevisions(signal),
    enabled: !daemonUnavailable,
  });
  const setups = useQuery({
    queryKey: ["setup-definitions"],
    queryFn: ({ signal }) => getSetupDefinitions(signal),
    enabled: !daemonUnavailable,
  });
  const current = versions.data?.items.find((item) => item.id === selected);
  const entities = [
    ...new Map(
      (setups.data?.items.flatMap((item) => item.definition.topology.entities ?? []) ?? []).map(
        (item) => [`${item.kind}:${item.id}`, item],
      ),
    ).values(),
  ];
  if (daemonUnavailable)
    return <p>Reconnect to the application to manage setups and parameters.</p>;
  return (
    <section className="grid gap-4">
      <header>
        <h2>Experiment configuration</h2>
        <p>
          Setups bind registered devices. Parameter versions hold scientific inputs; neither changes
          another page's selection.
        </p>
        <label>
          Operator
          <input value={operator} onChange={(event) => setOperator(event.target.value)} />
        </label>
      </header>
      <SetupPanel operator={operator} onSelectConfiguration={onSelectConfiguration} />
      <section
        className="grid gap-3 rounded-lg border border-line bg-panel p-4"
        aria-label="Parameter versions"
      >
        <h3>Parameter versions</h3>
        <p>
          Start from an imported template or a saved version. Unknown values remain unknown; saving
          a draft does not validate a calibration.
        </p>
        <label>
          Saved parameter version
          <select
            value={selected}
            onChange={(event) => {
              setSelected(event.target.value);
              setEditing(undefined);
            }}
          >
            <option value="">Choose a version</option>
            {versions.data?.items.map((item) => (
              <option key={item.id} value={item.id}>
                {item.id}
              </option>
            ))}
          </select>
        </label>
        {versions.error && <p role="alert">{errorMessage(versions.error)}</p>}
        {current && (
          <>
            <p>
              {current.actor}
              {current.note ? ` · ${current.note}` : ""}
            </p>
            <div className="flex gap-3">
              <button onClick={() => setEditing(structuredClone(current))}>Edit a copy</button>
              <button
                onClick={() =>
                  onSelectConfiguration?.({
                    kind: "parameters",
                    ref: { revision_id: current.id, content_hash: current.content_hash },
                    overrides: [],
                  })
                }
              >
                Use for next experiment
              </button>
            </div>
          </>
        )}
        {editing && (
          <ParameterVersionEditor
            key={editing.id}
            base={editing}
            entities={entities}
            operator={operator}
            onCancel={() => setEditing(undefined)}
            onSaved={async (saved) => {
              setSelected(saved.id);
              setEditing(undefined);
              await cache.invalidateQueries({ queryKey: ["parameter-revisions"] });
              await cache.invalidateQueries({ queryKey: ["parameter-branches"] });
            }}
          />
        )}
      </section>
    </section>
  );
}

function ParameterVersionEditor({
  base,
  entities,
  operator,
  onCancel,
  onSaved,
}: {
  base: ParameterRevision;
  entities: ParameterEntity[];
  operator: string;
  onCancel: () => void;
  onSaved: (saved: ParameterRevision) => Promise<void>;
}) {
  const [name, setName] = useState(`${base.id} (revised)`);
  const [note, setNote] = useState("");
  const [branch, setBranch] = useState("");
  const [values, setValues] = useState<StoredParameterValue[]>(base.parameters.values ?? []);
  const heads = useQuery({
    queryKey: ["parameter-branches", "editing"],
    queryFn: ({ signal }) =>
      apiData(
        apiClient.GET("/api/v1/parameters/branches", { params: { query: { limit: 100 } }, signal }),
      ),
  });
  const reviewedHead = useQuery({
    queryKey: ["parameter-branch-review", branch.trim()],
    queryFn: ({ signal }) =>
      apiData(
        apiClient.GET("/api/v1/parameters/branches/{name}", {
          params: { path: { name: branch.trim() } },
          signal,
        }),
      ),
    enabled: !!branch.trim(),
    retry: false,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnMount: false,
  });
  const newBranch = reviewedHead.error instanceof ApiError && reviewedHead.error.status === 404;
  const head = reviewedHead.data;
  const setValue = (id: string, value?: StoredParameterValue) =>
    setValues((current) =>
      value === undefined
        ? current.filter((item) => item.id !== id)
        : [...current.filter((item) => item.id !== id), value],
    );
  const save = useMutation({
    mutationFn: async () => {
      const command = {
        revision_id: name.trim(),
        actor: operator,
        note,
        catalog: base.catalog,
        parameters: { ...base.parameters, values },
      };
      if (branch.trim()) {
        if (!head && !newBranch) throw new Error("Review the branch head before saving.");
        const result = await commitParameterBranch({
          name: branch.trim(),
          expected_generation: head?.generation ?? 0,
          source: command,
          actor: operator,
          note,
        });
        return result.revision;
      }
      const result = await saveParameterRevision(command);
      return { revision_id: result.id, content_hash: result.content_hash };
    },
    onSuccess: async (ref) => {
      const saved = await apiData(
        apiClient.GET("/api/v1/parameters/revisions/{revision_id}", {
          params: { path: { revision_id: ref.revision_id } },
        }),
      );
      await onSaved(saved);
    },
  });
  return (
    <section aria-label="Edit parameter version" className="grid gap-3 border-t border-line pt-3">
      <label>
        New version name
        <input value={name} onChange={(event) => setName(event.target.value)} />
      </label>
      {(base.catalog.definitions ?? []).map((definition) => {
        const value = values.find((item) => item.id === definition.id);
        if (definition.value_type.shape === "scalar")
          return (
            <ParameterValueField
              key={definition.id}
              label={definition.id}
              type={definition.value_type.atom}
              value={value?.shape === "scalar" ? value.value : undefined}
              entities={entities}
              onChange={(atom) =>
                setValue(
                  definition.id,
                  atom === undefined
                    ? undefined
                    : { id: definition.id, shape: "scalar", value: atom },
                )
              }
            />
          );
        if (definition.value_type.shape !== "table")
          return (
            <p key={definition.id}>{definition.id}: edit this parameter shape through Python.</p>
          );
        const table = definition.value_type;
        const rows = value?.shape === "table" ? (value.rows ?? []) : [];
        return (
          <fieldset key={definition.id} className="grid gap-2 rounded border border-line p-3">
            <legend>{definition.id}</legend>
            {value === undefined && <p>Unknown table</p>}
            {rows.map((row, index) => (
              <div key={index} className="grid gap-2 border-t border-line py-2">
                {table.columns.map((column) => (
                  <ParameterValueField
                    key={column.id}
                    label={`${definition.id}[${index + 1}].${column.id}`}
                    type={column.value_type}
                    value={row[column.id]}
                    entities={entities}
                    onChange={(atom) => {
                      const next = { ...row };
                      if (atom === undefined) delete next[column.id];
                      else next[column.id] = atom;
                      setValue(definition.id, {
                        id: definition.id,
                        shape: "table",
                        rows: rows.map((item, position) => (position === index ? next : item)),
                      });
                    }}
                  />
                ))}
                <button
                  onClick={() =>
                    setValue(definition.id, {
                      id: definition.id,
                      shape: "table",
                      rows: rows.filter((_, position) => position !== index),
                    })
                  }
                >
                  Remove row {index + 1}
                </button>
              </div>
            ))}
            <div className="flex gap-3">
              <button
                onClick={() =>
                  setValue(definition.id, {
                    id: definition.id,
                    shape: "table",
                    rows: [...rows, {}],
                  })
                }
              >
                Add row
              </button>
              <button onClick={() => setValue(definition.id)}>Mark table unknown</button>
            </div>
          </fieldset>
        );
      })}
      <label>
        Source or reason for changes
        <input value={note} onChange={(event) => setNote(event.target.value)} />
      </label>
      <label>
        Named branch (optional)
        <input
          list="parameter-branch-names"
          value={branch}
          onChange={(event) => setBranch(event.target.value)}
        />
      </label>
      <datalist id="parameter-branch-names">
        {heads.data?.items.map((item) => (
          <option key={item.name} value={item.name}>
            {item.name}
          </option>
        ))}
      </datalist>
      {branch && (
        <p>
          {head
            ? `Update ${branch} from generation ${head.generation}.`
            : newBranch
              ? `Create branch ${branch}.`
              : "Reading branch head…"}{" "}
          A concurrent update requires review before retrying.{" "}
          <button onClick={() => void reviewedHead.refetch()}>Review latest branch head</button>
        </p>
      )}
      {(save.error || heads.error || (reviewedHead.error && !newBranch)) && (
        <p role="alert">{errorMessage(save.error ?? heads.error ?? reviewedHead.error)}</p>
      )}
      <div className="flex gap-3">
        <button onClick={onCancel}>Cancel</button>
        <button
          disabled={
            !name.trim() || !operator.trim() || save.isPending || (!!branch && !head && !newBranch)
          }
          onClick={() => save.mutate()}
        >
          Save parameter version
        </button>
      </div>
    </section>
  );
}
