"""Analysis subscriptions and their append-only progress journal."""

import sqlite3
from typing import cast

from scopecat.records.analysis_follow import (
    AnalysisFollowEvent,
    AnalysisFollowPage,
    AnalysisFollowRequest,
    AnalysisFollowState,
    AnalysisFollowView,
)

from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.data_cleanup import require_retained_resource


class AnalysisFollowRepository:
    def __init__(self, sqlite: SQLiteDatabase) -> None:
        self.sqlite = sqlite

    def create(self, request: AnalysisFollowRequest) -> AnalysisFollowView:
        with self.sqlite.write_transaction() as connection:
            require_retained_resource(connection, "run", request.analysis.run_id)
            previous = cast(
                "sqlite3.Row | None",
                connection.execute(
                    "SELECT request_json FROM analysis_follows WHERE id=?",
                    (request.id,),
                ).fetchone(),
            )
            if previous is not None:
                if (
                    AnalysisFollowRequest.model_validate_json(cast("str", previous[0]))
                    != request
                ):
                    raise ValueError("analysis follow id already has different inputs")
            else:
                count = cast(
                    "int",
                    connection.execute(
                        "SELECT COUNT(*) FROM analysis_follows WHERE state='running'"
                    ).fetchone()[0],
                )
                if count >= 8:
                    raise ValueError(
                        "eight analysis follows are already active; stop one first"
                    )
                connection.execute(
                    "INSERT INTO analysis_follows(id,run_id,request_json,state) "
                    "VALUES(?,?,?,'running')",
                    (request.id, request.analysis.run_id, request.model_dump_json()),
                )
        return self.get(request.id)

    def get(self, identity: str) -> AnalysisFollowView:
        with self.sqlite.read_transaction() as connection:
            return self._get(connection, identity)

    def _get(self, connection: sqlite3.Connection, identity: str) -> AnalysisFollowView:
        row = cast(
            "sqlite3.Row | None",
            connection.execute(
                "SELECT * FROM analysis_follows WHERE id=?", (identity,)
            ).fetchone(),
        )
        if row is None:
            raise KeyError(identity)
        states = self.states_in_transaction(connection, identity)
        return AnalysisFollowView(
            request=AnalysisFollowRequest.model_validate_json(
                cast("str", row["request_json"])
            ),
            state=cast("AnalysisFollowState", row["state"]),
            group_count=cast("int | None", row["group_count"]),
            error=cast("str | None", row["error"]),
            finished_count=sum(state != "running" for state in states.values()),
            active_group=next(
                (index for index, state in states.items() if state == "running"), None
            ),
            failed_count=sum(
                state not in {"running", "succeeded"} for state in states.values()
            ),
        )

    def states_in_transaction(
        self, connection: sqlite3.Connection, identity: str
    ) -> dict[int, str]:
        rows = cast(
            "list[sqlite3.Row]",
            connection.execute(
                "SELECT e.group_index,e.state FROM analysis_follow_events e "
                "JOIN (SELECT group_index,MAX(sequence) AS latest "
                "FROM analysis_follow_events WHERE follow_id=? GROUP BY group_index) l "
                "ON e.sequence=l.latest",
                (identity,),
            ).fetchall(),
        )
        return {
            cast("int", row["group_index"]): cast("str", row["state"]) for row in rows
        }

    def states(self, identity: str) -> dict[int, str]:
        with self.sqlite.read_transaction() as connection:
            return self.states_in_transaction(connection, identity)

    def event(self, identity: str, event: AnalysisFollowEvent) -> None:
        with self.sqlite.write_transaction() as connection:
            connection.execute(
                "INSERT INTO analysis_follow_events "
                "(follow_id,group_index,state,event_json) VALUES(?,?,?,?)",
                (identity, event.group_index, event.state, event.model_dump_json()),
            )

    def set_state(
        self, identity: str, state: AnalysisFollowState, error: str | None = None
    ) -> None:
        with self.sqlite.write_transaction() as connection:
            connection.execute(
                "UPDATE analysis_follows SET state=?,error=? "
                "WHERE id=? AND state='running'",
                (state, error, identity),
            )

    def scan(self, identity: str) -> int:
        with self.sqlite.read_connection() as connection:
            return cast(
                "int",
                connection.execute(
                    "SELECT scan_index FROM analysis_follows WHERE id=?", (identity,)
                ).fetchone()[0],
            )

    def advance_scan(self, identity: str, index: int, count: int | None = None) -> None:
        with self.sqlite.write_transaction() as connection:
            connection.execute(
                "UPDATE analysis_follows SET scan_index=?, "
                "group_count=COALESCE(?,group_count) WHERE id=?",
                (index, count, identity),
            )

    def running(self) -> tuple[str, ...]:
        with self.sqlite.read_connection() as connection:
            return tuple(
                cast("str", row[0])
                for row in cast(
                    "list[sqlite3.Row]",
                    connection.execute(
                        "SELECT id FROM analysis_follows WHERE state='running' "
                        "ORDER BY sequence LIMIT 8"
                    ).fetchall(),
                )
            )

    def for_run(self, run_id: str) -> tuple[AnalysisFollowView, ...]:
        with self.sqlite.read_connection() as connection:
            rows = cast(
                "list[sqlite3.Row]",
                connection.execute(
                    "SELECT id FROM analysis_follows WHERE run_id=? "
                    "ORDER BY sequence DESC LIMIT 100",
                    (run_id,),
                ).fetchall(),
            )
        return tuple(self.get(cast("str", row[0])) for row in rows)

    def page(
        self, identity: str, *, after: int = 0, limit: int = 50
    ) -> AnalysisFollowPage:
        with self.sqlite.read_transaction() as connection:
            rows = cast(
                "list[sqlite3.Row]",
                connection.execute(
                    "SELECT sequence,event_json FROM analysis_follow_events "
                    "WHERE follow_id=? AND sequence>? ORDER BY sequence LIMIT ?",
                    (identity, after, limit + 1),
                ).fetchall(),
            )
            follow = self._get(connection, identity)
        events = tuple(
            AnalysisFollowEvent.model_validate_json(
                cast("str", row["event_json"])
            ).model_copy(update={"cursor": row["sequence"]})
            for row in rows[:limit]
        )
        return AnalysisFollowPage(
            follow=follow,
            events=events,
            next_cursor=events[-1].cursor if events else after,
            has_more=len(rows) > limit,
        )
