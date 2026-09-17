"""Preinstalled parameter declaration shared by course editing and experiments."""

import scopecat as sc


class Drive(sc.ParameterModel, table="teaching_drive"):
    """Frequency is expressed in GHz; this input is not a measured result."""

    id: sc.Param[str] = sc.param(key=True)
    frequency: sc.Magnitude[float] = sc.quantity(unit="GHz", minimum=4, maximum=8)
