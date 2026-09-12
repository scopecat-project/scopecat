# Edit parameter tables with JSON or pandas

Export an editable copy of a table from a named parameter workspace. Import first
produces a diff; applying it edits that workspace, and saving creates a named
version. It never activates a default or supplies verified calibration evidence.

## JSON: a portable editable file

```python
from pathlib import Path

params = author.config.workspace(context="my-working-point")
drive = params[Drive]
path = Path("drive.json")
path.write_text(drive.export_json(), encoding="utf-8")
```

Edit the `rows` array in the file. Keep `format`, `definition`, `context`, and `base`
unchanged: they describe the table schema, units and exact exported draft. Then:

```python
pending = drive.preview_json(path.read_text(encoding="utf-8"))
for change in pending.diff:
    print(change.key, change.field, change.before, change.after)
pending.apply()
print(params.diff())
version = params.save("drive-import")
```

`preview_json()` validates the whole import without changing the workspace.
`apply()` checks the base again before editing. If this table was edited or the
workspace was saved since export/preview, export again and review the new base.
An export is intended for its originating context, not automatic cross-project
parameter transfer or configuration migration.

Only changed cells are applied. Untouched cells keep their stored unit
representation and origin. Imported changes are manual edits; copying metadata
from a managed analysis cannot turn them into verified candidates.

## Cell and row rules

| Input | Meaning |
| --- | --- |
| A numeric zero | A known zero, subject to the declared bounds |
| JSON `null` | Explicitly clear the cell to unknown |
| An omitted non-key field | Keep the existing value; leave it unknown in a new row |
| An omitted row | Keep it, unless `delete_missing=True` is explicitly requested |
| A new row | Insert it; every primary-key field is required |

Unknown required fields can remain unknown while editing. A later experiment
that consumes one still stops with a field-specific error. Model constructor
defaults never fill imported omissions. Duplicate keys, extra columns, invalid
choices, incompatible units and out-of-range values are rejected.

Quantity columns export numbers in the **stored schema's unit**, recorded in
`definition`; a model view's alternate display unit does not change that contract.
Entity keys retain their kind and metadata. String identifiers such as `"001"`
remain strings. To rename keys or columns, or change units, use explicit workspace
structure edits rather than editing the exchange metadata.

Deleting rows requires `drive.preview_json(text, delete_missing=True)` and review
of the resulting row deletions. Applying a deletion invalidates earlier row views.

## Optional pandas editing

Install pandas in the environment used by the author kernel if it is not already
available. JSON exchange does not import or require pandas.

```python
frame = drive.to_dataframe()
frame.loc[frame["qubit"] == "q0", "duration"] = 96
pending = drive.preview_dataframe(frame)
print(pending.diff)
pending.apply()
version = params.save("drive-spreadsheet-edit")
```

The exported frame uses object columns to preserve identifiers and unknown cells.
Keys remain ordinary columns; the DataFrame index is ignored. Keep
`frame.attrs["scopecat.parameters"]`: transformations that discard it must start
again from a fresh export. CSV round trips do not preserve this metadata; use JSON
for portable files.

`None` and `pd.NA` explicitly clear cells. Floating-point `NaN` is rejected because
it can mean missing data or an invalid numerical result. If the intended meaning
is unknown, opt in with `preview_dataframe(frame, nan_as_unknown=True)` and review
the diff. Row deletion uses the same explicit `delete_missing=True` option.

See [parameter declarations](declare-parameter-models.md) for typed rows and
[candidate verification](verify-parameter-candidates.md) for measured updates
that need managed provenance and publication.
