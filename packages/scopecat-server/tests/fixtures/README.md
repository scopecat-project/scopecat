# Retained development schema fixture

`schema-68.sql` is the full SQLite schema extracted from public commit
`f9dd0762051c642be4a4324ea9edfc745a857ad5`, before the parameter-head change.
It is independent of current schema constants. Schema 69 adds the parameter-head
table from `1f94353c19c9beb59abde2a5a90989d58e5d2fbd`.

Migration tests seed synthetic evidence into that retained schema. The managed
reference-lab journey uses unchanged run/config/source/measurement/analysis record
formats to exercise reading, new analysis and actual restore. This is bounded
**development** compatibility evidence, not a designated stable release baseline
or proof of physical scientific validity.
