"""Host-side evaluation for config-dependent frontend constants."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from scopecat.compiler.relations.context import EvalContext, ParameterRelationData
from scopecat.compiler.relations.evaluation import evaluate_scalar, evaluate_table_value
from scopecat.compiler.relations.parameter_reads import ParameterReadRecorder
from scopecat.compiler.relations.verification import (
    ExpressionTypeBindings,
    verify_scalar_expression,
)
from scopecat.compiler.topology_selection import TopologyTableResolution
from scopecat.kernel.value_data import CellValue, Row
from scopecat.kernel.value_types import Scalar, Table
from scopecat.program.expressions import ScalarExpr
from scopecat.program.table_values import (
    TableSource,
    TopologyConnectionSetSource,
    TopologyEntitySetSource,
)


def _static_bindings(bindings: ExpressionTypeBindings) -> ExpressionTypeBindings:
    """Exclude the point row, which does not exist during host evaluation."""

    return ExpressionTypeBindings(
        inputs=bindings.inputs,
        parameters=bindings.parameters,
    )


@dataclass(frozen=True, slots=True)
class StaticRelationEvaluator:
    """Evaluate verified config-time relations."""

    parameters: ParameterRelationData
    topology_entity_sets: Mapping[
        TopologyEntitySetSource | TopologyConnectionSetSource,
        TopologyTableResolution,
    ]
    parameter_reads: ParameterReadRecorder | None = None

    def scalar(
        self,
        expression: ScalarExpr,
        *,
        bindings: ExpressionTypeBindings,
        inputs: Mapping[str, object],
        expected_type: Scalar | None = None,
    ) -> CellValue:
        selected_bindings = _static_bindings(bindings)
        verified = verify_scalar_expression(
            expression,
            bindings=selected_bindings,
            expected_type=expected_type,
        )
        return evaluate_scalar(
            verified,
            EvalContext(
                params=self.parameters,
                inputs=dict(inputs),
                parameter_reads=self.parameter_reads,
            ),
            bindings=selected_bindings,
            expected_type=expected_type,
        )

    def table(
        self,
        source: TableSource,
        value_type: Table,
        *,
        inputs: Mapping[str, object],
    ) -> list[Row]:
        """Evaluate a direct table source during configuration binding."""

        if isinstance(source, TopologyEntitySetSource | TopologyConnectionSetSource):
            return list(self.topology_entity_sets[source].table.rows)
        return evaluate_table_value(
            source,
            value_type,
            EvalContext(
                params=self.parameters,
                inputs=dict(inputs),
                parameter_reads=self.parameter_reads,
            ),
        )
