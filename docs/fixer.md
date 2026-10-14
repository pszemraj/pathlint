# Experimental fixer: supported calls and limitations

**Review every diff and test the result.** Pathlib normalizes path strings.
The fixer does not promise to remove every violation.

```bash
pathlint fix src/ --dry-run
pathlint fix src/
```

## Supported rewrites

These examples use `Path` for readability; the generated constructor may use
an alias:

| Call | Pathlib replacement |
| --- | --- |
| `os.path.isabs("file")` | `Path("file").is_absolute()` |
| `os.path.join("src", "file.py")` | `str(Path("src", "file.py"))` |
| `os.path.basename("src/file.py")` | `Path("src/file.py").name` |
| `os.path.dirname("src/file.py")` | `str(Path("src/file.py").parent)` |

Fixes use AST locations to change expressions while preserving surrounding
source text, including comments and strings. Imported aliases, named function
imports, calls inside nested expressions, and expressions inside f-strings are
supported. Pathlib imports follow module docstrings and future imports. Existing
Pathlib aliases are reused where possible; new constructor imports use a private
alias to preserve wildcard exports.

Supported calls include `isabs`, `join`, `basename`, `dirname`, and `expanduser`
with string-literal arguments, subject to the restrictions below. In particular,
all tilde-prefixed `expanduser` arguments remain unchanged, so home-directory
expansion is not currently migrated. Calls with nonliteral arguments, including
variables and nested calls, remain unchanged; eligible literal calls inside them
can be fixed. Values and types are not inferred.

## Calls requiring manual migration

`abspath`, `splitext`, `sep`, and `pathsep` require manual migration; the fixer
does not supply behavior-preserving replacements. `exists`, `isfile`, and `isdir`
are also left unchanged: on supported Python versions, their Pathlib replacements
can raise filesystem errors where `os.path` returns `False`.

## Path forms and unchanged calls

Keyword arguments, starred arguments, call targets containing comments, literal
file descriptors, byte paths, and known inexact literal forms are left unchanged.
Known inexact forms include empty paths, trailing separators, named-user
expansion, and rooted literals passed to `isabs` whose interpretation can differ
under Windows path rules.

All tilde-prefixed `expanduser` arguments remain unchanged because home values
can contain spellings that Pathlib normalizes or fail to resolve.

### Windows paths

On Windows, string-returning fixes skip literals whose separators or dot
components Pathlib would normalize.

## Bindings and annotations

Calls with uncertain branch, exception, context manager, match, or loop bindings
are left unchanged. Fixes in deferred bodies require enclosing imports to exist
when the function, lambda, or generator is created, and conservatively skip
aliases rebound anywhere in the file, including imports with conflicting origins.
Stable imports inside the body can still be migrated.

The fixer leaves annotation expressions unchanged: postponed annotations and
Python 3.14's lazy annotations can resolve names after their bindings change.
Calls outside annotation expressions remain eligible for fixes.

Explicit `global` writes prevent affected aliases, constructors, and `str`
conversions from being used by fixes, including in module-level code.

## Mutation and escaped objects

Methods whose `os.path` attributes or callable internals are assigned or deleted
are left unchanged. Mutation checks retain possible copies of imported modules
and callables through assignments, loop targets, and default parameters. They
inspect all expression children, conservatively including container selections
and comprehensions. Calls retain possible origins from their arguments; return
types are not inferred.

Copies stored in object attributes, class fields, or container entries
conservatively prevent fixes for the copied module or callable. The same applies
when these objects are passed to calls, returned, yielded, or used by context
managers or match patterns. These copies do not establish bindings for detection
or migration of copied calls.

Mutation origins are conservative across scopes and statement order; copies of
callable internals can also exclude fixes for their exported parent. Assigning an
imported module's `__class__` invalidates all of its lookups because module
subclasses can override attributes with descriptors. Fixes avoid constructors
affected by Pathlib mutations.

## Dynamic namespaces

Sources using dynamic code, namespace dictionaries, or dynamic import/attribute
builtins are left unchanged. These capabilities can overwrite aliases or
generated constructors:

- `exec`, `eval`, `globals`, `locals`, `vars`, `__import__`, `getattr`, `setattr`,
  and `delattr`;
- their bound `__setattr__`/`__delattr__` equivalents;
- exposed `builtins` and function/frame namespaces;
- module registries (`sys.modules` or `importlib.import_module`).

Exposed function defaults, keyword defaults, and annotation dictionaries also
prevent fixes. Bare `dir()` use retains imports while supported calls remain
eligible.

Sources containing wildcard imports are left unchanged because their exports
can overwrite names used by a fix. Sources using type parameters or type aliases
are also left unchanged because their annotation scopes and lazy values are not
analyzed by the fixer.

## Retained imports

Imports needed by unsupported references are retained. Fixes retain module-level
imports for re-exports, including conditional imports and bindings declared
`global`. Imports referenced by annotations or `__all__` are retained too.
Retained imports can appear in check results. Unused local imports can be removed.

Return to the [README](../README.md).
