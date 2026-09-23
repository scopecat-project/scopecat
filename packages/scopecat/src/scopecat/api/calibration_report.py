"""Notebook presentation of a captured capability report; never queries on display."""

from html import escape
from typing import override

from scopecat.daemon.calibration_checks import CalibrationReport


class CalibrationReportView(CalibrationReport):
    """Keep typed report fields while giving interactive users a readable snapshot."""

    def _rows(self) -> list[tuple[str, ...]]:
        return [
            (
                item.requirement.id,
                item.requirement.scope.capability,
                ", ".join(item.requirement.scope.targets),
                item.selection.status,
                item.availability.status,
                ", ".join(item.availability.blocked_by) or "—",
            )
            for item in self.items
        ]

    @property
    def _notice(self) -> str:
        return (
            f"Observed at {self.observed_at.isoformat()}. "
            "Snapshot of declared requirements only; not overall sample readiness. "
            "Call lab.calibration_checks.report(...) again to refresh."
        )

    @override
    def __repr__(self) -> str:
        return "\n".join(
            [
                self._notice,
                (
                    "Requirement | Capability | Targets | Check | "
                    "Availability | Blocked by"
                ),
            ]
            + [" | ".join(row) for row in self._rows()]
        )

    def _repr_html_(self) -> str:
        headings = (
            "Requirement",
            "Capability",
            "Targets",
            "Own check",
            "Availability",
            "Blocked by",
        )
        header = "".join(f"<th scope='col'>{name}</th>" for name in headings)
        rows = "".join(
            "<tr>" + "".join(f"<td>{escape(cell)}</td>" for cell in row) + "</tr>"
            for row in self._rows()
        )
        details: list[str] = []
        for item in self.items:
            requirement = item.requirement
            evidence = item.selection.evidence
            reasons = (
                item.selection.reason,
                *(
                    item.selection.assessment.reasons
                    if item.selection.assessment
                    else ()
                ),
                *item.incomplete_reasons,
            )
            facts = [
                (
                    f"Conditions: {requirement.scope.conditions}; "
                    f"policy: {requirement.scope.policy_version}"
                ),
                f"Maximum age: {requirement.max_age}; inspected checks: {item.scanned}",
                "Declared prerequisites: "
                + (", ".join(requirement.depends_on) or "none"),
                f"Reasons: {', '.join(reasons)}",
            ]
            if evidence is not None:
                facts.extend(
                    (
                        f"Measurement: {evidence.measurement.run_id}",
                        "Measurement time: "
                        + evidence.measurement.created_at.isoformat(),
                        f"Analysis: {evidence.analysis_record_id or 'none'}",
                    )
                )
            if item.unresolved_procedures:
                facts.append(
                    f"Unresolved executions: {', '.join(item.unresolved_procedures)}"
                )
            details.append(
                f"<details><summary>{escape(requirement.id)} evidence</summary><ul>"
                + "".join(f"<li>{escape(fact)}</li>" for fact in facts)
                + "</ul></details>"
            )
        context = self.context
        context_text = (
            f"Parameters: {context.parameters.revision_id} "
            f"({context.parameters.content_hash})\n"
            f"Setup: {context.setup_content_hash}\n"
            f"Subject: {context.subject.model_dump_json()}\n"
            f"Scenario: {context.scenario!r}"
        )
        return (
            f"<p>{escape(self._notice)}</p>"
            f"<table><thead><tr>{header}</tr></thead><tbody>{rows}</tbody></table>"
            + "".join(details)
            + "<details><summary>Exact measurement context</summary>"
            + f"<pre>{escape(context_text)}</pre></details>"
        )
