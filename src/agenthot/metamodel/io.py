"""Load/save .ecore metamodels and .xmi model instances via pyecore."""
from __future__ import annotations

from pathlib import Path

from pyecore.ecore import EObject, EPackage
from pyecore.resources import ResourceSet, URI


def save_ecore(package: EPackage, path: str | Path) -> None:
    rs = ResourceSet()
    resource = rs.create_resource(URI(str(path)))
    resource.append(package)
    resource.save()


def load_ecore(path: str | Path, rs: ResourceSet | None = None) -> EPackage:
    rs = rs or ResourceSet()
    resource = rs.get_resource(URI(str(path)))
    return resource.contents[0]


def save_model(root: EObject, path: str | Path, *, resource_set: ResourceSet | None = None) -> None:
    rs = resource_set or ResourceSet()
    resource = rs.create_resource(URI(str(path)))
    resource.append(root)
    resource.save()


def load_model(path: str | Path, package: EPackage, *, resource_set: ResourceSet | None = None) -> EObject:
    rs = resource_set or ResourceSet()
    rs.metamodel_registry[package.nsURI] = package
    resource = rs.get_resource(URI(str(path)))
    return resource.contents[0]
