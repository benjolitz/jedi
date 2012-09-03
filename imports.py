from __future__ import with_statement

import os
import pkgutil
import imp
import sys
import weakref

import builtin
import modules
import debug
import parsing
import evaluate


class ModuleNotFound(Exception):
    pass


class ImportPath(object):
    """
    An ImportPath is the path of a `parsing.Import` object.
    """
    class GlobalNamespace(object):
        pass

    def __init__(self, import_stmt, is_like_search=False):
        self.import_stmt = import_stmt
        self.import_path = []
        if import_stmt.from_ns:
            self.import_path += import_stmt.from_ns.names
        if import_stmt.namespace:
            if self.is_nested_import():
                self.import_path.append(import_stmt.namespace.names[0])
            else:
                self.import_path += import_stmt.namespace.names

        self.is_like_search = is_like_search
        if is_like_search:
            # drop one path part, because that is used by the like search
            self.import_path.pop()

        self.file_path = os.path.dirname(import_stmt.get_parent_until().path)

    def __repr__(self):
        return '<%s: %s>' % (self.__class__.__name__, self.import_stmt)

    def is_nested_import(self):
        """
        This checks for the special case of nested imports, without aliases and
        from statement:
        >>> import foo.bar
        """
        return not self.import_stmt.alias and not self.import_stmt.from_ns \
                and len(self.import_stmt.namespace.names) > 1

    def get_nested_import(self, parent):
        """
        See documentation of `self.is_nested_import`.
        Generates an Import statement, that can be used to fake nested imports.
        """
        i = self.import_stmt
        # This is not an existing Import statement. Therefore, set position to
        # None.
        zero = (None, None)
        n = parsing.Name(i.namespace.names[1:], zero, zero)
        new = parsing.Import(zero, zero, n)
        new.parent = weakref.ref(parent)
        evaluate.faked_scopes.append(new)
        debug.dbg('Generated a nested import: %s' % new)
        return new

    def get_defined_names(self):
        names = []
        for scope in self.follow():
            if scope is ImportPath.GlobalNamespace:
                names += self.get_module_names()
                names += self.get_module_names([self.file_path])
            else:
                for s, n in evaluate.get_names_for_scope(scope,
                                                    include_builtin=False):
                    names += n
        return names

    def get_module_names(self, search_path=None):
        """
        Get the names of all modules in the search_path. This means file names
        and not names defined in the files.
        """
        names = []
        for module_loader, name, is_pkg in pkgutil.iter_modules(search_path):
            inf = float('inf')
            names.append(parsing.Name([name], (inf, inf), (inf, inf)))
        return names

    def follow(self):
        """
        Returns the imported modules.
        """
        if self.import_path:
            scope, rest = self.follow_file_system()
            if len(rest) > 1 or rest and self.is_like_search:
                scopes = []
            elif rest:
                scopes = list(evaluate.follow_path(iter(rest), scope))
            else:
                scopes = [scope]

            new = []
            for scope in scopes:
                new += remove_star_imports(scope)
            scopes += new

            if self.is_nested_import():
                scopes.append(self.get_nested_import(scope))
        else:
            scopes = [ImportPath.GlobalNamespace]
        debug.dbg('after import', scopes)
        return scopes

    def follow_file_system(self):
        """
        Find a module with a path (of the module, like usb.backend.libusb10).
        """
        def follow_str(ns, string):
            debug.dbg('follow_module', ns, string)
            path = None
            if ns:
                path = ns[1]
            elif self.import_stmt.relative_count:
                module = self.import_stmt.get_parent_until()
                path = os.path.abspath(module.path)
                for i in range(self.import_stmt.relative_count):
                    path = os.path.dirname(path)

            if path is not None:
                return imp.find_module(string, [path])
            else:
                debug.dbg('search_module', string, self.file_path)
                # Override the sys.path. It works only good that way.
                # Injecting the path directly into `find_module` did not work.
                sys.path, temp = builtin.module_find_path, sys.path
                try:
                    i = imp.find_module(string)
                except:
                    sys.path = temp
                    raise
                sys.path = temp
                return i

        current_namespace = None
        builtin.module_find_path.insert(0, self.file_path)
        # now execute those paths
        rest = []
        for i, s in enumerate(self.import_path):
            try:
                current_namespace = follow_str(current_namespace, s)
            except ImportError:
                if current_namespace:
                    rest = self.import_path[i:]
                else:
                    raise ModuleNotFound(
                            'The module you searched has not been found')

        builtin.module_find_path.pop(0)
        path = current_namespace[1]
        is_package_directory = current_namespace[2][2] == imp.PKG_DIRECTORY

        f = None
        if is_package_directory or current_namespace[0]:
            # is a directory module
            if is_package_directory:
                path += '/__init__.py'
                with open(path) as f:
                    source = f.read()
            else:
                source = current_namespace[0].read()
            if path.endswith('.py'):
                f = modules.Module(path, source)
            else:
                f = builtin.Parser(path=path)
        else:
            f = builtin.Parser(name=path)

        return f.parser.module, rest


def strip_imports(scopes):
    """
    Here we strip the imports - they don't get resolved necessarily.
    Really used anymore? Merge with remove_star_imports?
    """
    result = []
    for s in scopes:
        if isinstance(s, parsing.Import):
            # this is something like a statement following.
            evaluate.statement_path.append(s)
            try:
                result += ImportPath(s).follow()
            except ModuleNotFound:
                debug.warning('Module not found: ' + str(s))
        else:
            result.append(s)
    return result


def remove_star_imports(scope):
    """
    Check a module for star imports:
    >>> from module import *

    and follow these modules.
    """
    modules = strip_imports(i for i in scope.get_imports() if i.star)
    new = []
    for m in modules:
        new += remove_star_imports(m)
    modules += new

    # Filter duplicate modules.
    return set(modules)
