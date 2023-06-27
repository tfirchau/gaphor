"""The code generator for modeling languages.

This is the code generator for the models used by Gaphor.

In order to work with the code generator, a model should follow some conventions:

* `Profile` packages are only for profiles (excluded from generation)
* A stereotype `simpleAttribute` can be defined, which converts an association
  to a `str` attribute
* A stereotype attribute `subsets` can be defined in case an association is derived

The coder first write the class declarations, including attributes and enumerations.
After that, associations are filled in, including derived unions and redefines.

Notes:
* Enumerations are classes ending with "Kind" or "Sort".

The code generator works by reading a model and the models it depends on.
It defines classes, attributes, enumerations and associations. Class names
are considered unique.
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import sys
import textwrap
from pathlib import Path
from typing import Iterable

from gaphor import UML
from gaphor.codegen.override import Overrides
from gaphor.core.modeling import Element, ElementFactory
from gaphor.core.modeling.modelinglanguage import (
    CoreModelingLanguage,
    MockModelingLanguage,
    ModelingLanguage,
)
from gaphor.entrypoint import initialize
from gaphor.storage import storage
from gaphor.SysML.modelinglanguage import SysMLModelingLanguage
from gaphor.UML.modelinglanguage import UMLModelingLanguage

log = logging.getLogger(__name__)

header = textwrap.dedent(
    """\
    # This file is generated by coder.py. DO NOT EDIT!
    # {}: skip_file
    # {}: noqa F401,F811
    # fmt: off

    from __future__ import annotations

    from gaphor.core.modeling.properties import (
        association,
        attribute as _attribute,
        derived,
        derivedunion,
        enumeration as _enumeration,
        redefine,
        relation_many,
        relation_one,
    )

    """.format(
        "isort", "flake8"
    )  # work around tooling triggers
)


def main(
    modelfile: str,
    supermodelfiles: list[tuple[str, str]] | None = None,
    overridesfile: str | None = None,
    outfile: str | None = None,
):
    logging.basicConfig()

    extra_langs = (
        [
            load_modeling_language(lang)
            for lang, _ in supermodelfiles
            if lang not in ("Core", "UML", "SysML")
        ]
        if supermodelfiles
        else []
    )
    modeling_language = MockModelingLanguage(
        *(
            [CoreModelingLanguage(), UMLModelingLanguage(), SysMLModelingLanguage()]
            + extra_langs
        )
    )

    model = load_model(modelfile, modeling_language)
    super_models = (
        [
            (load_modeling_language(lang), load_model(f, modeling_language))
            for lang, f in supermodelfiles
        ]
        if supermodelfiles
        else []
    )
    overrides = Overrides(overridesfile) if overridesfile else None

    with open(outfile, "w", encoding="utf-8") if outfile else contextlib.nullcontext(sys.stdout) as out:  # type: ignore[attr-defined]
        for line in coder(model, super_models, overrides):
            print(line, file=out)


def load_model(modelfile: str, modeling_language: ModelingLanguage) -> ElementFactory:
    element_factory = ElementFactory()
    with open(modelfile, encoding="utf-8") as file_obj:
        storage.load(
            file_obj,
            element_factory,
            modeling_language,
        )

    resolve_attribute_type_values(element_factory)

    return element_factory


def load_modeling_language(lang) -> ModelingLanguage:
    return initialize("gaphor.modelinglanguages", [lang])[lang]


def coder(
    model: ElementFactory,
    super_models: list[tuple[ModelingLanguage, ElementFactory]],
    overrides: Overrides | None,
) -> Iterable[str]:
    classes = list(
        order_classes(
            c
            for c in model.select(UML.Class)
            if not is_enumeration(c)
            and not is_simple_type(c)
            and not is_in_profile(c)
            and not is_tilde_type(c)
        )
    )

    yield header
    if overrides and overrides.header:
        yield overrides.header

    already_imported = set()
    for c in classes:
        if overrides and overrides.has_override(c.name):
            yield overrides.get_override(c.name)
            continue

        element_type, cls = in_super_model(c.name, super_models)
        if element_type and cls:
            line = f"from {element_type.__module__} import {element_type.__name__}"
            yield line
            already_imported.add(line)
            continue

        yield class_declaration(c)
        if properties := list(variables(c, overrides)):
            yield from (f"    {p}" for p in properties)
        else:
            yield "    pass"
        yield ""
        yield ""

    for c in classes:
        yield from operations(c, overrides)

    yield ""

    for c in classes:
        yield from associations(c, overrides)
        for line in subsets(c, super_models):
            if line.startswith("from "):
                if line not in already_imported:
                    yield line
                already_imported.add(line)
            else:
                yield line


def class_declaration(class_: UML.Class):
    base_classes = ", ".join(
        c.name for c in sorted(bases(class_), key=lambda c: c.name)
    )
    return f"class {class_.name}({base_classes}):"


def variables(class_: UML.Class, overrides: Overrides | None = None):
    if class_.ownedAttribute:
        for a in sorted(class_.ownedAttribute, key=lambda a: a.name or ""):
            if is_extension_end(a):
                continue

            full_name = f"{class_.name}.{a.name}"
            if overrides and overrides.has_override(full_name):
                yield f"{a.name}: {overrides.get_type(full_name)}"
            elif a.isDerived and not a.type:
                log.warning(f"Derived attribute {full_name} has no implementation.")
            elif a.typeValue:
                yield f'{a.name}: _attribute[{a.typeValue}] = _attribute("{a.name}", {a.typeValue}{default_value(a)})'
            elif is_enumeration(a.type):
                enum_values = ", ".join(f'"{e.name}"' for e in a.type.ownedAttribute)
                yield f'{a.name} = _enumeration("{a.name}", ({enum_values}), "{a.type.ownedAttribute[0].name}")'
            elif a.type:
                mult = "one" if a.upper == "1" else "many"
                comment = "  # type: ignore[assignment]" if is_reassignment(a) else ""
                yield f"{a.name}: relation_{mult}[{a.type.name}]{comment}"
            else:
                raise ValueError(
                    f"{a.name}: {a.type} can not be written; owner={a.owner.name}"
                )

    if class_.ownedOperation:
        for o in sorted(class_.ownedOperation, key=lambda a: a.name or ""):
            full_name = f"{class_.name}.{o.name}"
            if overrides and overrides.has_override(full_name):
                yield f"{o.name}: {overrides.get_type(full_name)}"
            else:
                log.warning(f"Operation {full_name} has no implementation")


def associations(
    c: UML.Class,
    overrides: Overrides | None = None,
):
    redefinitions = []
    for a in c.ownedAttribute:
        full_name = f"{c.name}.{a.name}"
        if overrides and overrides.has_override(full_name):
            yield overrides.get_override(full_name)
        elif (
            not a.type
            or is_simple_type(a.type)
            or is_enumeration(a.type)
            or is_extension_end(a)
        ):
            continue
        elif redefines(a):
            redefinitions.append(
                f'{full_name} = redefine({c.name}, "{a.name}", {a.type.name}, {redefines(a)})'
            )
        elif a.isDerived:
            yield f'{full_name} = derivedunion("{a.name}", {a.type.name}{lower(a)}{upper(a)})'
        elif not a.name:
            raise ValueError(f"Unnamed attribute: {full_name} ({a.association})")
        else:
            yield f'{full_name} = association("{a.name}", {a.type.name}{lower(a)}{upper(a)}{composite(a)}{opposite(a)})'

    yield from redefinitions


def subsets(
    c: UML.Class,
    super_models: list[tuple[ModelingLanguage, ElementFactory]],
):
    for a in c.ownedAttribute:
        if (
            not a.type
            or is_simple_type(a.type)
            or is_enumeration(a.type)
            or is_extension_end(a)
        ):
            continue
        for slot in a.appliedStereotype[:].slot:
            if slot.definingFeature.name != "subsets":
                continue

            full_name = f"{c.name}.{a.name}"
            for value in slot.value.split(","):
                element_type, d = attribute(c, value.strip(), super_models)
                if d and d.isDerived:
                    if element_type:
                        yield f"from {element_type.__module__} import {d.owner.name}"  # type: ignore[attr-defined]
                    yield f"{d.owner.name}.{d.name}.add({full_name})  # type: ignore[attr-defined]"  # type: ignore[attr-defined]
                elif not d:
                    log.warning(
                        f"{full_name} wants to subset {value.strip()}, but it is not defined"
                    )
                else:
                    log.warning(
                        f"{full_name} wants to subset {value.strip()}, but it is not a derived union"
                    )


def operations(c: UML.Class, overrides: Overrides | None = None):
    if c.ownedOperation:
        for o in sorted(c.ownedOperation, key=lambda a: a.name or ""):
            full_name = f"{c.name}.{o.name}"
            if overrides and overrides.has_override(full_name):
                yield overrides.get_override(full_name)


def default_value(a):
    if a.defaultValue:
        if a.typeValue == "int":
            defaultValue = a.defaultValue.title()
        elif a.typeValue == "str":
            defaultValue = f'"{a.defaultValue}"'
        else:
            raise ValueError(
                f"Unknown default value type: {a.owner.name}.{a.name}: {a.typeValue} = {a.defaultValue}"
            )

        return f", default={defaultValue}"
    return ""


def lower(a):
    return "" if a.lowerValue in (None, "0") else f", lower={a.lowerValue}"


def upper(a):
    return "" if a.upperValue in (None, "*") else f", upper={a.upperValue}"


def composite(a):
    return ", composite=True" if a.aggregation == "composite" else ""


def opposite(a):
    return (
        f', opposite="{a.opposite.name}"'
        if a.opposite and a.opposite.name and a.opposite.class_
        else ""
    )


def order_classes(classes: Iterable[UML.Class]) -> Iterable[UML.Class]:
    seen_classes = set()

    def order(c):
        if c not in seen_classes:
            for b in bases(c):
                yield from order(b)
            yield c
            seen_classes.add(c)

    for c in classes:
        yield from order(c)


def bases(c: UML.Class) -> Iterable[UML.Class]:
    for g in c.generalization:
        yield g.general

    for a in c.ownedAttribute:
        if a.association and a.name == "baseClass":
            yield a.association.ownedEnd.class_


def is_enumeration(c: UML.Class) -> bool:
    return c and c.name and (c.name.endswith("Kind") or c.name.endswith("Sort"))  # type: ignore[return-value]


def is_simple_type(c: UML.Class) -> bool:
    return any(
        s.name == "SimpleAttribute" for s in UML.recipes.get_applied_stereotypes(c)
    ) or any(is_simple_type(g.general) for g in c.generalization)


def is_tilde_type(c: UML.Class) -> bool:
    return c and c.name and c.name.startswith("~")  # type: ignore[return-value]


def is_extension_end(a: UML.Property):
    return isinstance(a.association, UML.Extension)


def is_reassignment(a: UML.Property) -> bool:
    def test(c: UML.Class):
        for attr in c.ownedAttribute:
            if attr.name == a.name:
                return True
        return any(test(base) for base in bases(c))

    return any(test(base) for base in bases(a.owner))  # type:ignore[arg-type]


def is_in_profile(c: UML.Class) -> bool:
    def test(p: UML.Package):
        return isinstance(p, UML.Profile) or (p.owningPackage and test(p.owningPackage))

    return test(c.owningPackage)  # type: ignore[no-any-return]


def is_in_toplevel_package(c: UML.Class, package_name: str) -> bool:
    def test(p: UML.Package):
        return (not p.owningPackage and p.name == package_name) or (
            p.owningPackage and test(p.owningPackage)
        )

    return test(c.owningPackage)  # type: ignore[no-any-return]


def redefines(a: UML.Property) -> str | None:
    return next(
        (
            slot.value
            for slot in a.appliedStereotype[:].slot
            if slot.definingFeature.name == "redefines"
        ),
        None,
    )


def attribute(
    c: UML.Class, name: str, super_models: list[tuple[ModelingLanguage, ElementFactory]]
) -> tuple[type[Element] | None, UML.Property | None]:
    for a in c.ownedAttribute:
        if a.name == name:
            return None, a

    for base in bases(c):
        element_type, a = attribute(base, name, super_models)
        if a:
            return element_type, a

    element_type, super_class = in_super_model(c.name, super_models)
    if super_class and c is not super_class:
        _, a = attribute(super_class, name, super_models)
        return element_type, a

    return None, None


def in_super_model(
    name: str, super_models: list[tuple[ModelingLanguage, ElementFactory]]
) -> tuple[type[Element], UML.Class] | tuple[None, None]:
    for modeling_language, factory in super_models:
        cls: UML.Class
        for cls in factory.select(  # type: ignore[assignment]
            lambda e: isinstance(e, UML.Class) and e.name == name
        ):
            if not (is_in_profile(cls) or is_enumeration(cls)):
                element_type = modeling_language.lookup_element(cls.name)
                assert (
                    element_type
                ), f"Type {cls.name} found in model, but not in generated model"
                return element_type, cls
    return None, None


def resolve_attribute_type_values(element_factory: ElementFactory) -> None:
    """Some model updates that are hard to do from Gaphor itself."""
    for prop in element_factory.select(UML.Property):
        if prop.typeValue in ("String", "str", "object"):
            prop.typeValue = "str"
        elif prop.typeValue in (
            "Integer",
            "int",
            "Boolean",
            "bool",
            "UnlimitedNatural",
        ):
            prop.typeValue = "int"
        elif c := next(
            element_factory.select(
                lambda e: isinstance(e, UML.Class) and e.name == prop.typeValue
            ),
            None,
        ):
            prop.type = c  # type: ignore[assignment]
            del prop.typeValue
            prop.aggregation = "composite"

        if prop.type and is_simple_type(prop.type):  # type: ignore[arg-type]
            prop.typeValue = "str"
            del prop.type

        if not prop.type and prop.typeValue not in ("str", "int", None):
            raise ValueError(f"Property value type {prop.typeValue} can not be found")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("modelfile", type=Path, help="Gaphor model filename")
    parser.add_argument(
        "-o", dest="outfile", type=Path, help="Python data model filename"
    )
    parser.add_argument("-r", dest="overridesfile", type=Path, help="Override filename")
    parser.add_argument(
        "-s",
        dest="supermodelfiles",
        type=str,
        action="append",
        help="Reference to dependent model file (e.g. UML:models/UML.gaphor)",
    )

    args = parser.parse_args()
    supermodelfiles = (
        [s.split(":") for s in args.supermodelfiles] if args.supermodelfiles else []
    )

    main(args.modelfile, supermodelfiles, args.overridesfile, args.outfile)
