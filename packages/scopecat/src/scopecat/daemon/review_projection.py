"""Pure preview-to-wire conversions shared by managed and notebook reviews."""

from __future__ import annotations

from typing import cast

from scopecat.control.models import PointCoordinateValue
from scopecat.daemon.reviews import ReviewInspectionView, ReviewPointView
from scopecat.planning.preview_models import (
    ExperimentPreviewDomainInspection,
    ExperimentPreviewPoint,
)


def inspection_point(point: ExperimentPreviewPoint) -> ReviewPointView:
    """Preserve typed coordinates and proposal identity in either author surface."""
    return ReviewPointView(
        point_index=point.point_index,
        coordinates=cast("dict[str, PointCoordinateValue]", point.coordinates),
        proposal_fingerprint=point.proposal_fingerprint,
        source=point.source,
    )


def inspection_domain(item: ExperimentPreviewDomainInspection) -> ReviewInspectionView:
    """Reuse the target-owned bounded inspection; never materialize more points."""
    return ReviewInspectionView(
        operation_id=item.operation_id,
        point_index=item.point_index,
        target_id=item.target_id,
        artifact_id=item.artifact_id,
        artifact_fingerprint=item.artifact_fingerprint,
        content=item.content,
    )
