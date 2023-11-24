# This file is generated by coder.py. DO NOT EDIT!
# ruff: noqa: F401, E402, F811
# fmt: off

from __future__ import annotations

from gaphor.core.modeling.properties import (
    association,
    derived,
    derivedunion,
    redefine,
    relation_many,
    relation_one,
)
from gaphor.core.modeling.properties import (
    attribute as _attribute,
)
from gaphor.core.modeling.properties import (
    enumeration as _enumeration,
)
from gaphor.UML.uml import Actor


class C4Person(Actor):
    description: _attribute[str] = _attribute("description", str)
    location: _attribute[str] = _attribute("location", str)


from gaphor.UML.uml import Package


class C4Container(Package):
    description: _attribute[str] = _attribute("description", str)
    location: _attribute[str] = _attribute("location", str)
    ownerContainer: relation_one[C4Container]
    owningContainer: relation_many[C4Container]
    technology: _attribute[str] = _attribute("technology", str)
    type: _attribute[str] = _attribute("type", str)


class C4Database(C4Container):
    pass



C4Container.ownerContainer = association("ownerContainer", C4Container, upper=1, opposite="owningContainer")
C4Container.owningContainer = association("owningContainer", C4Container, composite=True, opposite="ownerContainer")
from gaphor.UML.uml import NamedElement

NamedElement.namespace.add(C4Container.ownerContainer)  # type: ignore[attr-defined]
from gaphor.UML.uml import Namespace

Namespace.ownedMember.add(C4Container.owningContainer)  # type: ignore[attr-defined]
