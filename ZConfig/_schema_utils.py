##############################################################################
#
# Copyright (c) 2017 Zope Corporation and Contributors.
# All Rights Reserved.
#
# This software is subject to the provisions of the Zope Public License,
# Version 2.1 (ZPL).  A copy of the ZPL should accompany this distribution.
# THIS SOFTWARE IS PROVIDED "AS IS" AND ANY AND ALL EXPRESS OR IMPLIED
# WARRANTIES ARE DISCLAIMED, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF TITLE, MERCHANTABILITY, AGAINST INFRINGEMENT, AND FITNESS
# FOR A PARTICULAR PURPOSE.
#
##############################################################################
from __future__ import print_function

from abc import abstractmethod
import argparse
import itertools
import sys
import textwrap

import ZConfig.loader

from ZConfig._compat import AbstractBaseClass

from ZConfig.datatypes import null_conversion
from ZConfig.info import SectionType
from ZConfig.info import SectionInfo
from ZConfig.info import ValueInfo
from ZConfig.info import AbstractType


class _VisitorBuilder(object):

    def __init__(self):
        self.visitors = []

    def __call__(self, Type):
        def dec(func):
            self.visitors.append((Type, func))
            return func
        return dec

MARKER = object()

class AbstractSchemaFormatter(AbstractBaseClass):

    def __init__(self, schema, stream=None):
        self.stream = stream or sys.stdout
        self._dt = schema.registry.find_name

    def write(self, *args):
        print(*args, file=self.stream)

    @abstractmethod
    def esc(self, x):
        "Escape blocks of text if needed"

    def _dedent(self, text):
        # dedent the text to avoid producing unwanted
        # definition lists. The XML parser strips leading whitespace from
        # the first line, but preserves it for subsequent lines, so for dedent
        # to work we have to ignore that first line.
        texts = text.split("\n")
        if len(texts) > 1:
            trail = textwrap.dedent('\n'.join(texts[1:]))
            text = texts[0] + '\n' + trail
        return text

    @abstractmethod
    def item_list(self):
        "Context manager for listing description items"

    def _describing(self, description, after):
        if description is not MARKER:
            with self.described_as():
                self.description(description)
                if after:
                    after()

    @abstractmethod
    def describing(self, description=MARKER, after=None):
        "description term, optional body"

    def describing_name(self, concrete_name,
                        description=MARKER, datatype=None,
                        **kwargs):
        with self.describing(description):
            self.concrete_name(concrete_name)
            self.datatype(datatype)

            for k, v in sorted(kwargs.items()):
                if v:
                    self.write(self.esc("(%s: %s)" % (k, v)))

    def description(self, description):
        if description:
            self.write(self.esc(description))

    example = description

    @abstractmethod
    def described_as(self):
        "Description body context manager"

    @abstractmethod
    def abstract_name(self, name):
        "Abstract name"

    @abstractmethod
    def concrete_name(self, *name):
        "Concrete name"

    @abstractmethod
    def concrete_section_name(self, *name):
        "Name of a section a user can type in a config"

    def datatype(self, datatype):
        self.write("(%s)" % self._dt(datatype))

    @abstractmethod
    def body(self):
        "Context manager for the whole document"


class AbstractSchemaPrinter(AbstractBaseClass):


    def __init__(self, schema, stream=None, allowed_names=()):
        self.schema = schema
        stream = stream or sys.stdout
        self._explained = set()
        self._seen_typenames = set()
        self.fmt = self._schema_formatter(schema, stream)

        if allowed_names:
            iter_all = self._iter_schema_items
            allowed_names = {x.lower() for x in allowed_names}
            def filtered():
                for name, info in iter_all():
                    if name and name.lower() in allowed_names:
                        yield name, info

            self._iter_schema_items = filtered

    @abstractmethod
    def _schema_formatter(self, schema, stream):
        "Return a formatter"

    def _explain(self, st):
        if st.name in self._explained: # pragma: no cover
            return

        self._explained.add(st.name)

        self.fmt.description(st.description)
        self.fmt.example(getattr(st, 'example', None))

        for sub in st.getsubtypenames():
            with self.fmt.item_list():
                self.visit(None, st.getsubtype(sub))

    def _iter_schema_items(self):
        def everything():
            return itertools.chain(self.schema.itertypes(),
                                   self.schema)
        # The abstract types tend to be the most important. Since
        # we only document a concrete type the first time we find it,
        # and we can find extensions of abstract types beneath
        # the abstract type which is itself buried under a concrete section,
        # all the different permutations would be only documented once under
        # that section. By exposing these first, they get documented at the top-level,
        # and each concrete section that uses the abstract type gets a reference
        # to it.

        def abstract_sections(base):
            for name, info in base:
                if isinstance(info, SectionInfo):
                    if info.sectiontype.isabstract():
                        yield name, info

                    # XXX: This isn't catching everything. Witness the
                    # relstorage component.
                elif isinstance(info, SectionType):
                    for x in abstract_sections(info):
                        yield x
        return itertools.chain(abstract_sections(everything()), everything())

    def printSchema(self):
        # side-effect of building may be printing
        self.buildSchema()

    def buildSchema(self):
        seen = set() # prevent duplicates at the top-level
        # as we find multiple abstract types
        with self.fmt.body():
            with self.fmt.item_list():
                for name, info in self._iter_schema_items():
                    if info in seen:
                        continue
                    seen.add(info)
                    self.visit(name, info)

    TypeVisitor = _VisitorBuilder()
    visitors = TypeVisitor.visitors

    def visit(self, name, info):
        for t, f in self.visitors:
            if isinstance(info, t):
                f(self, name, info)
                break
        else:
            self._visit_default(name, info)

    @TypeVisitor(SectionType)
    def _visit_SectionType(self, name, info):
        if info.name in self._seen_typenames:
            return
        self._seen_typenames.add(info.name)
        with self.fmt.describing():
            if info.datatype is not null_conversion:
                self.fmt.concrete_section_name(info.name)
            else:
                self.fmt.abstract_name(info.name)
            self.fmt.datatype(info.datatype)

        with self.fmt.described_as():
            self.fmt.description(info.description)
            self.fmt.example(info.example)

            with self.fmt.item_list():
                for sub in info:
                    self.visit(*sub) # pragma: no cover


    @TypeVisitor(SectionInfo)
    def _visit_SectionInfo(self, name, info):
        st = info.sectiontype
        if st.isabstract():
            with self.fmt.describing(info.description, lambda: self._explain(st)):
                self.fmt.abstract_name(st.name)
                self.fmt.concrete_name(info.name)

        else:
            with self.fmt.describing():
                self.fmt.concrete_section_name(info.attribute, info.name)
                self.fmt.datatype(info.datatype)

            with self.fmt.described_as():
                with self.fmt.item_list():
                    for sub in info.sectiontype:
                        self.visit(*sub)


    @TypeVisitor(AbstractType)
    def _visit_AbstractType(self, name, info):
        with self.fmt.describing(info.description, lambda: self._explain(info)):
            self.fmt.abstract_name(info.name)

    def _visit_default(self, name, info):
        # KeyInfo or MultiKeyInfo
        default = info.getdefault()
        if isinstance(default, ValueInfo):
            default = default.value

        self.fmt.describing_name(info.name, info.description, info.datatype,
                                 default=default, metadefault=info.metadefault)

    del TypeVisitor


def load_schema(schema, package, package_file):
    if not package:
        schema_reader = argparse.FileType('r')(schema)
    else:
        schema_template = "<schema><import package='%s' file='%s' /></schema>" % (
            schema, package_file or 'component.xml')
        from ZConfig._compat import TextIO
        schema_reader = TextIO(schema_template)

    schema = ZConfig.loader.loadSchemaFile(schema_reader)
    return schema
