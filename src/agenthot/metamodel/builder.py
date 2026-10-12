"""Convenience builders for EMF-compatible (pyecore) view metamodels.

Every artifact an agent exchanges is a model M_i in M(MM_i) conforming to a
view metamodel MM_i (Definition 1 / Sec III-A). We use pyecore -- a pure
Python implementation of EMF/Ecore -- so metamodels are ordinary `.ecore`
resources and model instances are ordinary `.xmi` resources, exactly as in
a Java/EMF toolchain.
"""
from __future__ import annotations

from pyecore.ecore import EAttribute, EClass, EPackage, EReference, EString, EBoolean, EInt

_PRIMS = {
    "string": EString,
    "str": EString,
    "boolean": EBoolean,
    "bool": EBoolean,
    "int": EInt,
    "integer": EInt,
}


class MetamodelBuilder:
    """Builds one EPackage (= one view metamodel MM_i) from a compact spec."""

    def __init__(self, name: str, nsURI: str, nsPrefix: str | None = None) -> None:
        self.package = EPackage(name=name, nsURI=nsURI, nsPrefix=nsPrefix or name)
        self._classes: dict[str, EClass] = {}
        self._root_slot_for_type: dict[str, str] = {}
        self.root_name: str | None = None

    def eclass(self, name: str, *, super_types: list[str] | None = None) -> EClass:
        cls = EClass(name)
        for st in super_types or []:
            cls.eSuperTypes.append(self._classes[st])
        self._classes[name] = cls
        self.package.eClassifiers.append(cls)
        return cls

    def attribute(self, cls: EClass, name: str, type_: str = "string", *, many: bool = False, default=None) -> EAttribute:
        # pyecore derives "many" from upperBound, not from a `many=` kwarg.
        eattr = EAttribute(name, _PRIMS[type_.lower()], upper=-1 if many else 1)
        if default is not None:
            eattr.defaultValueLiteral = str(default)
        cls.eStructuralFeatures.append(eattr)
        return eattr

    def reference(self, cls: EClass, name: str, target: str | EClass, *, many: bool = False, containment: bool = True) -> EReference:
        # `target` is either a class name in this same package, or an EClass
        # from another builder's package (`other_mm.get('Type')`) for a
        # cross-metamodel reference (e.g. Sec!SecurityReview.operation ->
        # Arch!Operation).
        target_cls = self._classes[target] if isinstance(target, str) else target
        eref = EReference(name, target_cls, upper=-1 if many else 1, containment=containment)
        cls.eStructuralFeatures.append(eref)
        return eref

    def get(self, name: str) -> EClass:
        return self._classes[name]

    def add_root_slot(self, root_cls: EClass, feature_name: str, target_class_name: str, *, many: bool = True) -> EReference:
        """Register `root_cls.feature_name` as the containment slot new elements
        of `target_class_name` are appended into when a rule creates one."""
        self.root_name = root_cls.name
        eref = self.reference(root_cls, feature_name, target_class_name, many=many, containment=True)
        self._root_slot_for_type[target_class_name] = feature_name
        return eref

    def root_slot_for(self, type_name: str) -> str:
        return self._root_slot_for_type[type_name]

    def new(self, class_name: str, **kwargs):
        """Instantiate a model element of `class_name` with attribute/reference kwargs."""
        instance = self._classes[class_name]()
        for k, v in kwargs.items():
            setattr(instance, k, v)
        return instance
