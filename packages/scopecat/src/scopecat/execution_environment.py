"""Select installed execution dependencies without importing their Python modules."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, distribution

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


def execution_packages(requirements: tuple[str, ...]) -> dict[str, str]:
    """Dependencies of a retained backend worker, including the server."""
    return installed_packages(("scopecat", "scopecat-server", *requirements))


def author_packages(requirements: tuple[str, ...]) -> dict[str, str]:
    """Dependencies needed to import declarations in a user Python process."""
    return installed_packages(("scopecat", *requirements))


def installed_packages(requirements: tuple[str, ...]) -> dict[str, str]:
    """Resolve installed metadata, including active markers and transitive extras.

    This validates an existing installation, not a package installer or solver.
    Local imports and optional runtime choices must be declared by the maintainer.
    """
    pending = [Requirement(item) for item in requirements]
    visited: set[tuple[str, str]] = set()
    packages: dict[str, str] = {}
    while pending:
        requirement = pending.pop()
        if requirement.marker is not None and not requirement.marker.evaluate(
            {"extra": ""}
        ):
            continue
        if requirement.url is not None:
            raise ValueError(
                "execution dependencies must name installed distributions, not URLs"
            )
        name = canonicalize_name(requirement.name)
        try:
            dist = distribution(name)
        except PackageNotFoundError as error:
            raise ValueError(
                f"execution dependency is not installed: {name}"
            ) from error
        if not requirement.specifier.contains(dist.version, prereleases=True):
            raise ValueError(
                f"execution dependency {requirement} does not accept "
                f"installed {dist.version}"
            )
        packages[name] = dist.version
        for extra in ("", *sorted(requirement.extras)):
            if (name, extra) in visited:
                continue
            visited.add((name, extra))
            for raw in dist.requires or ():
                child = Requirement(raw)
                if child.marker is None or child.marker.evaluate({"extra": extra}):
                    # This edge was evaluated in its parent's extras context.
                    child.marker = None
                    pending.append(child)
    return dict(sorted(packages.items()))
