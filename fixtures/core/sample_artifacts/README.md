# Synthetic sample attachments

`diagram.png` depicts two connected synthetic sites; `notes.txt` states its scope.
`document.pdf` is a minimal one-page synthetic delivery document; `layout.json`
is the same kind of synthetic two-site description as structured JSON.
These contain no real laboratory geometry or private data. Import the bytes with
`lab.samples.import_artifact`, then include the returned SHA-256 reference in a
sample revision. This directory alone is source material, not an object-store
backup. `test_sample_artifact_delivery.py` exercises the delivered bytes and their
ownership through HTTP and a complete project snapshot/restore.
