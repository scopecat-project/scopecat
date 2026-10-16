"""Server-owned bindings written only after retained analysis returns successfully."""

import sqlite3
from typing import cast

from scopecat.records.author_revision import (
    AuthorAnalysisReceipt,
    AuthorAnalysisRequest,
)

from scopecat_server.storage.sqlite.connection import SQLiteDatabase

MANAGED_ANALYSIS_STATEMENTS = (
    """CREATE TABLE managed_analysis_publications (
        publication_sequence INTEGER NOT NULL REFERENCES analysis_publications(sequence)
            ON DELETE CASCADE,
        publication_hash TEXT NOT NULL,
        request_json TEXT NOT NULL,
        PRIMARY KEY (publication_sequence, request_json)
    )""",
    """CREATE INDEX managed_analysis_publications_hash
        ON managed_analysis_publications(publication_hash)""",
)
MANAGED_ANALYSIS_TABLES_SQL = ";\n".join(MANAGED_ANALYSIS_STATEMENTS) + ";"


class ManagedAnalysisRepository:
    def __init__(self, sqlite: SQLiteDatabase) -> None:
        self.sqlite = sqlite

    def retain(
        self, request: AuthorAnalysisRequest, receipt: AuthorAnalysisReceipt
    ) -> None:
        if receipt.code_revision != request.code_revision:
            raise ValueError("managed analysis returned a different source revision")
        identities = (
            receipt.analysis_id,
            *(g.analysis_id for g in receipt.groups if g.error is None),
        )
        with self.sqlite.write_transaction() as connection:
            for identity in identities:
                row = cast(
                    "sqlite3.Row | None",
                    connection.execute(
                        "SELECT sequence, publication_hash FROM analysis_publications "
                        "WHERE record_id=? AND subject_kind='run' AND run_id=?",
                        (identity, request.run_id),
                    ).fetchone(),
                )
                if row is None:
                    raise ValueError(
                        "managed analysis returned an unrelated publication"
                    )
                connection.execute(
                    "INSERT OR IGNORE INTO managed_analysis_publications "
                    "(publication_sequence,publication_hash,request_json) "
                    "VALUES(?,?,?)",
                    (row[0], row[1], request.model_dump_json()),
                )

    def contains(self, run_id: str, analysis_id: str, publication_hash: str) -> bool:
        with self.sqlite.read_connection() as connection:
            return (
                connection.execute(
                    "SELECT 1 FROM managed_analysis_publications m "
                    "JOIN analysis_publications p ON p.sequence=m.publication_sequence "
                    "WHERE p.run_id=? AND p.record_id=? AND p.publication_hash=? "
                    "AND m.publication_hash=p.publication_hash LIMIT 1",
                    (run_id, analysis_id, publication_hash),
                ).fetchone()
                is not None
            )
