# Detection details

Pathlint detects `os.path` imports, aliases, function references, calls, and
usage in annotations:

```python
import os
import os as system
import os.path as osp
from os import path as paths
from os.path import exists as present

os.path.exists("file")
system.path.join("directory", "file")
osp.isdir("directory")
paths.dirname(__file__)
present("file")
callback = os.path.join
```

## Binding resolution

Pathlint resolves imported names in function, class, and comprehension scopes
so ordinary local shadowing is not mistaken for `os.path`. Private aliases in
class scopes are left unresolved because Python mangles their names. Deleted
class locals and cleared exception aliases fall back to module globals.

Function and generator bodies use the enclosing scope's final bindings for
detection; a generator's first iterable is inspected immediately. Dynamic
rebinding can prevent resolution. Wildcard imports invalidate tracked bindings;
later explicit imports can restore them.

Detection inspects `if` branches independently and uses literal conditions
when merging bindings in the current scope. Loop bindings are merged
conservatively across iterations and `else` suites. This is a static check,
not full Python execution analysis.

## Annotations

Expressions in annotations are checked too. Valid `os.PathLike` annotations
are not `os.path` usage and are not flagged. The fixer leaves annotation
expressions unchanged: postponed annotations and Python 3.14's lazy annotations
can resolve names after their bindings change.

Detection does not imply that a use can be fixed. See the
[fixer limitations](fixer.md) for the stricter migration rules.

Return to the [README](../README.md).
