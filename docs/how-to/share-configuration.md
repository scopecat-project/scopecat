# Share saved configuration from the desktop

In **Configuration → Share and import configuration**, export one saved parameter
version with its definitions. Values are included by default as initial inputs;
turn them off to share definitions with unknown values. No acquired run is needed.
Save any edits in the ordinary editor before exporting.

You can include a saved setup and the active retained revision of an author code
folder. The latter is the retained revision, not unsaved disk edits. If no revision
is available, prepare the trusted source through Experiments first. Export does
not run it. Review custom setup fields and source files before sharing; they may
contain information entered by the author.

Review the selection, then save `configuration.json`. The desktop opens its normal
Save dialog and reports the chosen path or cancellation. In a browser, check the
browser's download destination. The file contains existing parameter, setup and
source records, not a database clone, run evidence or an executable plan. This
first slice exports the whole selected parameter version, not a subset of fields.

## Receive and create your own copy

Open the file under **Import configuration file**. Inspection verifies parameter
values against their definitions and source files against their manifest without
executing source code. Cancel leaves the store unchanged. **Keep original for
later** stores only the original, which can be reopened in the same panel.

Choose a new branch name and **Create my copy**. It creates ordinary editable
parameters and retains the original and derivation receipt in application data.
Existing names are rejected; choose another name. Retrying an unchanged request
uses the same operation identity. If the connection fails, the UI does not claim
that nothing happened: retry unchanged or reopen the original to inspect copies.

A setup is optional. If included, explicitly choose a current local device for
**every** imported role. Sender device IDs are never accepted as implicit local
bindings. Register missing devices through **Devices and drivers**, then reopen
the saved original. Import does not connect equipment. Parameters, setup and the
receipt either save together or roll back together.

If you choose parameters only, the setup stays in the original. This slice does
not append it to an already derived branch: reopen the original and create another
named copy with explicit bindings when you are ready. Saved parameter copies use
**Parameter versions** below; setups use **Experiment setups**. An already open
unsaved parameter draft remains intact.

## Attached source is a separate step

Read the attached file contents and dependency information before accepting source.
**Accept source and download** saves an inert ZIP; it does not register or load code.

1. Extract into a new folder.
2. In the desktop's **Application settings**, use the existing folder registration
   flow. Choose **Execution Python**, or explicitly prepare the folder's
   `pyproject.toml` environment. The browser console cannot manage local folders.
3. In **Experiments**, select that source and your local parameters/setup. Complete
   trusted loading and obtain a fresh preview before saving a plan or running.

No static method reconstruction, dynamic-control evaluation, dependency
installation or calibration qualification is implied by import. Imported values
remain initial inputs. The first slice does not exchange controls or plan records.

Files are limited to 16 MiB. Current-format application backup/restore retains
originals and derivation receipts. Schema 110 is a development format, with no
old-store migration or compatibility promise; see the
[data compatibility policy](../development/data-compatibility.md).
