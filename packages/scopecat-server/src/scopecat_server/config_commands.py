"""Read-only validation of transitional combined bootstrap declarations."""

from scopecat.config.resolution import compose_configuration
from scopecat.project import Project
from scopecat.records.config import ConfigProfileSnapshot


def load_source_config(project: Project) -> ConfigProfileSnapshot:
    """Freshly evaluate and validate the project's executable config source."""

    bootstrap = project.load_bootstrap()
    if bootstrap.setup is None or bootstrap.parameter_defaults is None:
        raise ValueError("project bootstrap must define setup and parameter_defaults")
    setup = bootstrap.setup()
    parameters = bootstrap.parameter_defaults()
    return compose_configuration(
        setup,
        id=parameters.id,
        system_id=parameters.system_id,
        catalog=parameters.catalog,
        parameters=parameters.parameters,
    )
