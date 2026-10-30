"""Static architecture discovery for Python projects using the standard `ast` module.

Output: a list of components and edges. Every edge carries evidence
(file, line, redacted snippet) and a classification:

  confirmed - the relationship is directly visible in the source
              (e.g. `sqlite3.connect(...)`, a decorator `@router.get("/x")`).
  inferred  - the relationship relies on a heuristic (e.g. a variable's type
              guessed from its assignment, a test-client URL matched to a route).

Anything the analyzer cannot resolve is counted in `coverage.unresolved_calls`
rather than guessed. Dynamic dispatch, reflection, monkeypatching, configuration-
driven wiring and inheritance across modules are NOT modelled.
"""
from __future__ import annotations

import ast
import builtins
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from app.analyzer.cache import FileCache, default_cache_dir
from app.analyzer.ingest import scan_tree
from app.analyzer.redact import find_secrets, redact

BUILTIN_NAMES = set(dir(builtins))
HTTP_VERBS = {"get", "post", "put", "patch", "delete", "options", "head"}
ROUTE_DECORATORS = HTTP_VERBS | {"route", "api_route", "websocket"}

DB_CALLS = {
    "sqlite3.connect": "sqlite",
    "sqlalchemy.create_engine": "sqlalchemy",
    "sqlalchemy.ext.asyncio.create_async_engine": "sqlalchemy",
    "psycopg2.connect": "postgresql",
    "psycopg.connect": "postgresql",
    "asyncpg.connect": "postgresql",
    "asyncpg.create_pool": "postgresql",
    "pymysql.connect": "mysql",
    "mysql.connector.connect": "mysql",
    "pymongo.MongoClient": "mongodb",
    "motor.motor_asyncio.AsyncIOMotorClient": "mongodb",
    "redis.Redis": "redis",
    "redis.StrictRedis": "redis",
    "redis.from_url": "redis",
}
HTTP_FUNCS = {f"{lib}.{v}" for lib in ("httpx", "requests") for v in HTTP_VERBS | {"request", "stream"}}
HTTP_FUNCS |= {"urllib.request.urlopen", "urllib3.request"}
HTTP_CLIENT_CTORS = {"httpx.Client", "httpx.AsyncClient", "requests.Session", "requests.session", "aiohttp.ClientSession", "urllib3.PoolManager"}
APP_CTORS = {"fastapi.FastAPI": "fastapi", "flask.Flask": "flask"}
ROUTER_CTORS = {"fastapi.APIRouter": "fastapi", "flask.Blueprint": "flask"}
THREAD_CTORS = {"threading.Thread", "multiprocessing.Process", "concurrent.futures.ThreadPoolExecutor"}
FRAMEWORK_IMPORTS = {
    "fastapi": "FastAPI", "flask": "Flask", "django": "Django", "sqlalchemy": "SQLAlchemy", "sqlite3": "sqlite3",
    "httpx": "httpx", "requests": "requests", "celery": "Celery", "pytest": "pytest", "pydantic": "Pydantic",
    "redis": "Redis", "pymongo": "MongoDB", "psycopg2": "PostgreSQL", "aiohttp": "aiohttp",
}
SQL_RE = re.compile(r"^\s*(SELECT|INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|WITH|REPLACE)\b", re.I)


# --------------------------------------------------------------------------- helpers
def dotted(node: ast.AST) -> list[str] | None:
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Attribute):
        base = dotted(node.value)
        return base + [node.attr] if base else None
    return None


def const_str(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def path_template(node: ast.AST | None) -> str | None:
    """String literal or f-string -> template with {x} placeholders."""
    s = const_str(node)
    if s is not None:
        return s
    if isinstance(node, ast.JoinedStr):
        out = []
        for v in node.values:
            if isinstance(v, ast.Constant):
                out.append(str(v.value))
            else:
                out.append("{x}")
        return "".join(out)
    return None


def handler_names(h: ast.ExceptHandler) -> list[str]:
    if h.type is None:
        return ["*"]
    elts = h.type.elts if isinstance(h.type, ast.Tuple) else [h.type]
    return [".".join(dotted(e) or ["?"]) for e in elts]


def raised_names(body: list[ast.stmt]) -> set[str]:
    out = set()
    for stmt in body:
        for n in ast.walk(stmt):
            if isinstance(n, ast.Raise) and n.exc is not None:
                target = n.exc.func if isinstance(n.exc, ast.Call) else n.exc
                d = dotted(target)
                if d:
                    out.add(d[-1])
    return out


def has_raise(body: list[ast.stmt]) -> bool:
    return any(isinstance(n, ast.Raise) for stmt in body for n in ast.walk(stmt))


def norm_path(p: str) -> str:
    p = p.split("?")[0]
    if not p.startswith("/"):
        p = "/" + p
    p = re.sub(r"/+", "/", p)
    return p.rstrip("/") or "/"


def paths_match(route: str, called: str) -> bool:
    a, b = norm_path(route).split("/"), norm_path(called).split("/")
    if len(a) != len(b):
        return False
    for x, y in zip(a, b):
        if x == y or (x.startswith("{") and x.endswith("}")) or (y.startswith("{") and y.endswith("}")):
            continue
        return False
    return True


# --------------------------------------------------------------------------- data
@dataclass
class FuncCtx:
    id: str
    qual: str
    node: ast.FunctionDef | ast.AsyncFunctionDef
    cls: str | None = None
    parent: str | None = None  # qual of the enclosing function (nested defs)
    local_types: dict = field(default_factory=dict)
    local_env: dict = field(default_factory=dict)
    local_defs: dict = field(default_factory=dict)  # name -> ("func"|"class", id) for nested defs
    local_imports: dict = field(default_factory=dict)  # local name -> fq dotted (function-local imports)
    calls: list = field(default_factory=list)  # (Call node, tries)
    env_reads: list = field(default_factory=list)  # (var, line, has_default, tries)
    patch_targets: list = field(default_factory=list)  # raw mock/patch targets seen in the body
    raises: set = field(default_factory=set)
    collected: bool = False
    inferred_return: tuple | None = None  # cached ("class"/"lib", value) factory inference


@dataclass
class ModuleInfo:
    name: str
    rel: str
    tree: ast.Module
    lines: list[str]
    is_test: bool
    functions: dict = field(default_factory=dict)  # qual -> FuncCtx
    classes: dict = field(default_factory=dict)  # class path -> ClassDef (dotted for nested classes)
    bindings: dict = field(default_factory=dict)  # local name -> fq dotted
    instances: dict = field(default_factory=dict)  # module var -> ast.Call
    aliases: dict = field(default_factory=dict)  # module var -> dotted ref (Impl = MyImpl)
    routers: dict = field(default_factory=dict)  # var -> {kind, framework, prefix, line, call}
    drf_routers: dict = field(default_factory=dict)  # var -> {"registrations": [(prefix, view_ref)]}
    has_urlpatterns: bool = False  # Django-style urls module
    module_env: dict = field(default_factory=dict)  # const name -> env var
    class_attr_types: dict = field(default_factory=dict)  # cls -> {attr: (kind, value)}
    class_attr_env: dict = field(default_factory=dict)  # cls -> {attr: VAR}
    class_bases: dict = field(default_factory=dict)


class Analyzer:
    def __init__(self, root: Path, tree: dict | None = None, cache: FileCache | None = None):
        self.root = Path(root)
        self.tree = tree
        self.cache = cache or FileCache(None, use_cache=False)
        self.modules: dict[str, ModuleInfo] = {}
        self.components: dict[str, dict] = {}
        self.edges: dict[str, dict] = {}
        self.parse_errors: list[dict] = []
        self.secret_hits: list[dict] = []
        self.frameworks: set[str] = set()
        self.third_party: set[str] = set()
        self.stats = {"resolved_calls": 0, "unresolved_calls": 0, "library_calls": 0, "functions": 0, "classes": 0}
        self.http_findings: list[dict] = []
        self.broad_excepts: list[dict] = []
        self.env_required: list[dict] = []
        self._fn_by_id: dict[str, tuple] = {}  # func id -> (ModuleInfo, FuncCtx) for factory inference

    # ------------------------------------------------------------- utilities
    def snippet(self, mod: ModuleInfo, line: int | None) -> str:
        if not line or line > len(mod.lines):
            return ""
        text, _ = redact(mod.lines[line - 1].strip())
        return text[:220]

    def ev(self, mod: ModuleInfo, line: int | None, detail: str = "", **extra) -> dict:
        return {"file": mod.rel, "line": line, "snippet": self.snippet(mod, line), "detail": detail, **extra}

    def add_component(self, cid: str, ctype: str, name: str, file: str | None = None, line: int | None = None, **data):
        if cid not in self.components:
            self.components[cid] = {"id": cid, "type": ctype, "name": name, "file": file, "line": line, "data": data}
        else:
            self.components[cid]["data"].update({k: v for k, v in data.items() if v is not None})
        return self.components[cid]

    def add_edge(self, source: str, target: str, etype: str, classification: str, evidence: dict,
                 guard: list | None = None, mocked: bool | None = None):
        if source == target:
            return
        eid = f"{etype}|{source}|{target}"
        e = self.edges.get(eid)
        if e is None:
            e = self.edges[eid] = {"id": eid, "source": source, "target": target, "type": etype,
                                   "classification": classification, "evidence": [], "call_sites": []}
        if classification == "confirmed":
            e["classification"] = "confirmed"
        if len(e["evidence"]) < 8:
            e["evidence"].append(evidence)
        e["call_sites"].append({"line": evidence.get("line"), "file": evidence.get("file"), "tries": guard or []})
        if mocked:
            e["mocked"] = True
        return e

    # ------------------------------------------------------------- pipeline
    def run(self, progress=None) -> dict:
        def step(name, frac):
            if progress:
                progress(name, frac)

        step("Scanning files", 0.05)
        if self.tree is None:
            self.tree = scan_tree(self.root)
        tree = self.tree
        py_files = [f for f in tree["files"] if f.endswith(".py")]

        step("Parsing Python sources", 0.15)
        for rel in py_files:
            self._load_module(rel)

        step("Indexing symbols", 0.3)
        for mod in self.modules.values():
            self._index_module(mod)

        step("Resolving routes and application wiring", 0.45)
        self._resolve_app_wiring()

        step("Resolving calls and resource usage", 0.6)
        for mod in self.modules.values():  # first pass: collect bodies so return-type
            if not mod.is_test:            # inference can look at already-colled callees
                for fn in mod.functions.values():
                    self._collect(mod, fn)
        for mod in self.modules.values():
            if not mod.is_test:
                for fn in mod.functions.values():
                    self._analyse_function(mod, fn)

        step("Mapping tests to components", 0.75)
        self._analyse_tests()

        step("Reading dependency manifests", 0.85)
        manifests = self._read_manifests(tree["manifests"])

        routes = [c for c in self.components.values() if c["type"] == "route"]
        tested_routes = {e["target"] for e in self.edges.values() if e["type"] == "exercises"}
        total_calls = self.stats["resolved_calls"] + self.stats["unresolved_calls"]
        coverage = {
            "python_files": len(py_files),
            "parsed_files": len(self.modules),
            "parse_errors": self.parse_errors,
            "functions": self.stats["functions"],
            "classes": self.stats["classes"],
            "resolved_calls": self.stats["resolved_calls"],
            "unresolved_calls": self.stats["unresolved_calls"],
            "library_calls": self.stats["library_calls"],
            "call_resolution_rate": round(self.stats["resolved_calls"] / total_calls, 3) if total_calls else None,
            "routes": len(routes),
            "routes_with_tests": len([r for r in routes if r["id"] in tested_routes]),
            "tests": len([c for c in self.components.values() if c["type"] == "test"]),
        }
        languages = tree["languages"]
        return {
            "components": list(self.components.values()),
            "edges": [{k: v for k, v in e.items()} for e in self.edges.values()],
            "scan": {
                "file_count": tree["file_count"],
                "languages": languages,
                "analysed_languages": ["Python"] if py_files else [],
                "manifests": tree["manifests"],
                "skipped_sensitive": len(tree["skipped_sensitive"]),
                "skipped_large": tree["skipped_large"],
                "incremental": dict(self.cache.stats),
            },
            "frameworks": sorted(self.frameworks),
            "third_party_imports": sorted(self.third_party),
            "dependencies": manifests,
            "coverage": coverage,
            "secret_hits": self.secret_hits,
            "signals": {
                "http_calls": self.http_findings,
                "broad_excepts": self.broad_excepts,
                "env_required": self.env_required,
            },
        }

    # ------------------------------------------------------------- loading
    def _module_name(self, rel: str) -> str:
        parts = rel[:-3].split("/")
        if parts[0] == "src" and len(parts) > 1:
            parts = parts[1:]
        if parts[-1] == "__init__":
            parts = parts[:-1]
        return ".".join(parts) or "__root__"

    def _load_module(self, rel: str):
        path = self.root / rel
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        # incremental: reuse the cached parse for unchanged files
        cached = self.cache.load(rel, source)
        if cached is not None:
            for hit in cached.get("secret_hits", []):
                self.secret_hits.append({**hit, "file": rel})
            if cached.get("parse_error"):
                self.parse_errors.append({"file": rel, **cached["parse_error"]})
                return
            tree = cached["tree"]
            lines = cached["lines"]
        else:
            try:
                tree = ast.parse(source, filename=rel)
            except SyntaxError as exc:
                self.parse_errors.append({"file": rel, "line": exc.lineno, "error": exc.msg})
                self.cache.misses += 1
                self.cache.store(rel, source, {"parse_error": {"line": exc.lineno, "error": exc.msg}})
                return
            lines = source.splitlines()
            hits = []
            for h in find_secrets(source):
                text, _ = redact(lines[h["line"] - 1].strip()) if 0 < h["line"] <= len(lines) else ("", None)
                hits.append({"line": h["line"], "snippet": text[:220]})
            self.cache.misses += 1
            self.cache.store(rel, source, {"tree": tree, "lines": lines, "secret_hits": hits})
            for hit in hits:
                self.secret_hits.append({**hit, "file": rel})
        name = self._module_name(rel)
        base = rel.split("/")[-1]
        is_test = base.startswith("test_") or base.endswith("_test.py") or base == "conftest.py"
        mod = ModuleInfo(name=name, rel=rel, tree=tree, lines=lines, is_test=is_test)
        self.modules[name] = mod

    def _resolve_relative(self, mod: ModuleInfo, level: int, module: str | None) -> str:
        pkg = mod.name.split(".")
        if not mod.rel.endswith("__init__.py"):
            pkg = pkg[:-1]
        if level > 1:
            pkg = pkg[: len(pkg) - (level - 1)]
        return ".".join([p for p in pkg + ([module] if module else []) if p])

    # ------------------------------------------------------------- indexing
    def _index_module(self, mod: ModuleInfo):
        if not mod.is_test:
            self.add_component(f"module:{mod.name}", "module", mod.name, mod.rel, 1)
        self._index_block(mod, mod.tree.body, cls_path=None, fn_qual=None)

    _BLOCK_STMTS = (ast.If, ast.While, ast.For, ast.AsyncFor, ast.With, ast.AsyncWith, ast.Try)

    def _index_block(self, mod: ModuleInfo, body, cls_path: str | None, fn_qual: str | None):
        """Index statements at any nesting level (module, class, function, conditional blocks)."""
        for node in body:
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.asname:
                        self._bind_import(mod, fn_qual, a.asname, a.name)
                    else:
                        self._bind_import(mod, fn_qual, a.name.split(".")[0], a.name.split(".")[0])
                    self._note_import(mod, a.name, node.lineno)
            elif isinstance(node, ast.ImportFrom):
                base = self._resolve_relative(mod, node.level, node.module) if node.level else (node.module or "")
                for a in node.names:
                    if a.name == "*":
                        continue
                    self._bind_import(mod, fn_qual, a.asname or a.name, f"{base}.{a.name}" if base else a.name)
                self._note_import(mod, base, node.lineno)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._add_function(mod, node, cls_path, fn_qual)
            elif isinstance(node, ast.ClassDef):
                self._add_class(mod, node, cls_path, fn_qual)
            elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                self._index_assign(mod, node)
            elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                self._index_module_call(mod, node.value)
            elif isinstance(node, self._BLOCK_STMTS):  # conditional/attempted defs and imports
                for stmt in self._block_bodies(node):
                    self._index_block(mod, stmt, cls_path, fn_qual)

    def _block_bodies(self, node) -> list[list[ast.stmt]]:
        out = []
        if isinstance(node, (ast.If, ast.While, ast.For, ast.AsyncFor)):
            out = [node.body, node.orelse]
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            out = [node.body]
        elif isinstance(node, ast.Try):
            out = [node.body, node.orelse, node.finalbody] + [h.body for h in node.handlers]
        return [b for b in out if b]

    def _bind_import(self, mod: ModuleInfo, fn_qual: str | None, local: str, fq: str):
        if fn_qual is None:
            mod.bindings[local] = fq
        else:
            fn = mod.functions.get(fn_qual)
            if fn is not None:
                fn.local_imports[local] = fq

    def _index_assign(self, mod: ModuleInfo, node):
        """Module-level assignments: instances, aliases, env consts, import-time resources."""
        if isinstance(node, ast.AugAssign):
            if isinstance(node.target, ast.Name) and node.target.id == "urlpatterns":
                mod.has_urlpatterns = True
            return
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        value = node.value
        for t in targets:
            if not isinstance(t, ast.Name) or value is None:
                continue
            if isinstance(value, ast.Call):
                mod.instances[t.id] = value
                self._import_time_resource(mod, t.id, value)
                parts = dotted(value.func)
                if parts and parts[-1] in ("DefaultRouter", "SimpleRouter"):
                    mod.drf_routers[t.id] = {"registrations": [], "line": value.lineno}
            elif isinstance(value, (ast.Name, ast.Attribute)):
                ref = dotted(value)
                if ref:
                    mod.aliases[t.id] = ".".join(ref)
            var = self._env_var_of(mod, value, None)
            if var:
                mod.module_env[t.id] = var[0]
            if t.id == "urlpatterns":
                mod.has_urlpatterns = True

    def _import_time_resource(self, mod: ModuleInfo, var: str, call: ast.Call):
        """DB clients constructed at import time get a module -> database edge."""
        r = self.class_of_call(mod, call, None)
        if not r or r[0] != "lib" or r[1] not in DB_CALLS:
            return
        engine = DB_CALLS[r[1]]
        db_id = f"database:{engine}"
        self.add_component(db_id, "database", f"{engine} database", None, None, engine=engine,
                           libraries=sorted(set(self.components.get(db_id, {}).get("data", {}).get("libraries", []))
                                            | {r[1].split('.')[0]}))
        self.add_edge(f"module:{mod.name}", db_id, "uses_database", "confirmed",
                      self.ev(mod, call.lineno, f"{r[1]}() created at import time (module-level '{var}')"))

    def _index_module_call(self, mod: ModuleInfo, call: ast.Call):
        """Module-level expression calls: import-time DB calls and DRF router registrations."""
        self._import_time_resource(mod, "_", call)
        parts = dotted(call.func) or []
        if len(parts) == 2 and parts[1] == "register" and parts[0] in mod.drf_routers and len(call.args) >= 2:
            prefix = const_str(call.args[0]) or ""
            ref = dotted(call.args[1])
            if ref:
                mod.drf_routers[parts[0]]["registrations"].append((prefix, ref, call.lineno))

    def _add_class(self, mod: ModuleInfo, node: ast.ClassDef, cls_path: str | None, fn_qual: str | None):
        path = f"{fn_qual}.<locals>.{node.name}" if fn_qual else \
            (f"{cls_path}.{node.name}" if cls_path else node.name)
        mod.classes[path] = node
        mod.class_bases[path] = [".".join(dotted(b) or []) for b in node.bases]
        if not mod.is_test:
            self.stats["classes"] += 1
            cid = f"class:{mod.name}.{path}"
            self.add_component(cid, "class", node.name, mod.rel, node.lineno, module=mod.name)
            parent = f"func:{mod.name}.{fn_qual}" if fn_qual else \
                (f"class:{mod.name}.{cls_path}" if cls_path else f"module:{mod.name}")
            self.add_edge(parent, cid, "contains", "confirmed", self.ev(mod, node.lineno))
        self._index_block(mod, node.body, cls_path=path, fn_qual=None)

    def _note_import(self, mod: ModuleInfo, name: str, line: int):
        top = name.split(".")[0] if name else ""
        if top in FRAMEWORK_IMPORTS:
            self.frameworks.add(FRAMEWORK_IMPORTS[top])
        if mod.is_test:
            return
        internal = self._internal_module(name)
        if internal and internal != mod.name:
            self.add_edge(f"module:{mod.name}", f"module:{internal}", "imports", "confirmed", self.ev(mod, line))
        elif not internal and top and top not in {"__future__"}:
            self.third_party.add(top)

    def _internal_module(self, name: str) -> str | None:
        parts = name.split(".")
        for i in range(len(parts), 0, -1):
            cand = ".".join(parts[:i])
            if cand in self.modules:
                return cand
        return None

    def _add_function(self, mod: ModuleInfo, node, cls_path: str | None, fn_qual: str | None = None):
        if cls_path:
            qual = f"{cls_path}.{node.name}"
        elif fn_qual:
            qual = f"{fn_qual}.<locals>.{node.name}"
        else:
            qual = node.name
        fid = f"func:{mod.name}.{qual}"
        ctx = FuncCtx(id=fid, qual=qual, node=node, cls=cls_path, parent=fn_qual)
        mod.functions[qual] = ctx
        self._fn_by_id[fid] = (mod, ctx)
        if mod.is_test:
            self._index_block(mod, node.body, cls_path=None, fn_qual=qual)
            return
        self.stats["functions"] += 1
        self.add_component(fid, "function", qual, mod.rel, node.lineno, module=mod.name,
                           is_async=isinstance(node, ast.AsyncFunctionDef), cls=cls_path,
                           args=[a.arg for a in node.args.args if a.arg not in ("self", "cls")])
        parent = f"class:{mod.name}.{cls_path}" if cls_path else \
            (f"func:{mod.name}.{fn_qual}" if fn_qual else f"module:{mod.name}")
        self.add_edge(parent, fid, "contains", "confirmed", self.ev(mod, node.lineno))
        if cls_path:
            self._index_self_attrs(mod, cls_path, node)
        self._index_block(mod, node.body, cls_path=None, fn_qual=qual)

    def _index_self_attrs(self, mod: ModuleInfo, cls: str, node):
        types = mod.class_attr_types.setdefault(cls, {})
        envs = mod.class_attr_env.setdefault(cls, {})
        for n in ast.walk(node):
            if isinstance(n, ast.Assign):
                for t in n.targets:
                    if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == "self":
                        if isinstance(n.value, ast.Call):
                            types[t.attr] = ("call", n.value)
                        var = self._env_var_of(mod, n.value, None)
                        if var:
                            envs[t.attr] = var[0]

    # ------------------------------------------------------------- symbol resolution
    def resolve_symbol(self, fq: str, depth: int = 0):
        """fq dotted name -> (kind, value). kinds: func, class, module, instance, lib."""
        if depth > 6:
            return None
        parts = fq.split(".")
        for i in range(len(parts), 0, -1):
            modname = ".".join(parts[:i])
            if modname not in self.modules:
                continue
            mod = self.modules[modname]
            rest = parts[i:]
            if not rest:
                return ("module", modname)
            head = rest[0]
            if head in mod.classes:
                cls_fq = f"{modname}.{head}"
                if len(rest) == 1:
                    return ("class", cls_fq)
                if len(rest) == 2:
                    m = self.find_method(cls_fq, rest[1])
                    return ("func", m) if m else None
                return None
            if head in mod.functions and len(rest) == 1:
                return ("func", mod.functions[head].id)
            if head in mod.instances:
                inst = self.class_of_call(mod, mod.instances[head], None)
                if inst and inst[0] == "class":
                    if len(rest) == 1:
                        return ("instance", inst[1])
                    if len(rest) == 2:
                        m = self.find_method(inst[1], rest[1])
                        return ("func_inferred", m) if m else None
                if inst and inst[0] == "lib":
                    return ("lib_instance", inst[1])
                return None
            if head in mod.bindings:
                return self.resolve_symbol(".".join([mod.bindings[head]] + rest[1:]), depth + 1)
            return None
        return ("lib", fq)

    def _split_class_fq(self, cls_fq: str) -> tuple[str | None, str]:
        """'pkg.mod.A.B' -> ('pkg.mod', 'A.B') via longest module-name prefix.

        Needed because nested classes make the class path contain dots, so a
        plain rpartition at the last dot would split inside the class path.
        """
        cls_clean = cls_fq[6:] if cls_fq.startswith("class:") else cls_fq
        parts = cls_clean.split(".")
        for i in range(len(parts) - 1, 0, -1):
            modname = ".".join(parts[:i])
            if modname in self.modules:
                return modname, ".".join(parts[i:])
        return None, cls_clean

    def find_method(self, cls_fq: str, name: str, depth: int = 0) -> str | None:
        cls_clean = cls_fq[6:] if cls_fq.startswith("class:") else cls_fq
        modname, cls = self._split_class_fq(cls_clean)
        mod = self.modules.get(modname) if modname else None
        if not mod or depth > 4:
            return None
        if f"{cls}.{name}" in mod.functions:
            return mod.functions[f"{cls}.{name}"].id
        for base in mod.class_bases.get(cls, []):
            r = self.resolve_name(mod, base.split("."), None)
            if r and r[0] == "class":
                m = self.find_method(r[1], name, depth + 1)
                if m:
                    return m
        return None

    def resolve_name(self, mod: ModuleInfo, parts: list[str], fn: FuncCtx | None, depth: int = 0):
        """Resolve a dotted reference used inside `mod` (and optionally function `fn`)."""
        if depth > 6 or not parts:
            return None
        head = parts[0]
        if fn is not None and head in fn.local_types:
            kind, val = fn.local_types[head]
            if kind == "class" and len(parts) == 2:
                m = self.find_method(val, parts[1])
                return ("func_inferred", m) if m else None
            if kind == "func" and len(parts) == 1:
                return ("func", val)
            if kind == "lib" and len(parts) >= 2:
                return ("lib_method", val, parts[-1])
            return None
        if fn is not None and head in fn.local_defs:
            kind, val = fn.local_defs[head]
            if kind == "func" and len(parts) == 1:
                return ("func", val)
            if kind == "class":
                if len(parts) == 1:
                    return ("class", val)
                m = self.find_method(val, parts[1]) if len(parts) == 2 else None
                return ("func", m) if m else None
        if head in mod.functions and len(parts) == 1:
            return ("func", mod.functions[head].id)
        if head in mod.classes:
            cls_fq = f"{mod.name}.{head}"
            if len(parts) == 1:
                return ("class", cls_fq)
            m = self.find_method(cls_fq, parts[1]) if len(parts) == 2 else None
            return ("func", m) if m else None
        if head in mod.instances and head not in mod.bindings:
            inst = self.class_of_call(mod, mod.instances[head], None)
            if inst and inst[0] == "class" and len(parts) == 2:
                m = self.find_method(inst[1], parts[1])
                return ("func_inferred", m) if m else None
            if inst and inst[0] == "lib" and len(parts) >= 2:
                return ("lib_method", inst[1], parts[-1])
            if inst and len(parts) == 1:
                return ("instance", inst[1])
            return None
        if head in mod.aliases and not (fn is not None and head in fn.local_imports):
            # statically resolvable DI/config wiring: Impl = MyImpl, Client = settings-driven class
            aliased = mod.aliases[head].split(".") + parts[1:]
            if aliased[:len(parts)] != parts:
                return self.resolve_name(mod, aliased, fn, depth + 1)
            return None
        binding_fq = None
        if fn is not None and head in fn.local_imports:
            binding_fq = ".".join([fn.local_imports[head]] + parts[1:])
        elif head in mod.bindings:
            binding_fq = ".".join([mod.bindings[head]] + parts[1:])
        if binding_fq is not None:
            r = self.resolve_symbol(binding_fq)
            if r is None:
                return None
            if r[0] == "lib_instance" and len(parts) >= 2:
                return ("lib_method", r[1], parts[-1])
            return r
        return None

    def class_of_call(self, mod: ModuleInfo, call: ast.Call, fn: FuncCtx | None, depth: int = 0):
        """What a Call constructs: ("class", fq), ("lib", fq), or a factory's inferred product."""
        parts = dotted(call.func)
        if not parts:
            return None
        r = self.resolve_name(mod, parts, fn)
        if r and r[0] == "class":
            return ("class", r[1])
        if r and r[0] == "lib":
            return ("lib", r[1])
        if r and r[0] in ("func", "func_inferred") and r[1] and depth <= 4:
            return self._infer_factory_return(mod, r[1], depth)
        return None

    def _infer_factory_return(self, mod: ModuleInfo, fid: str, depth: int = 0):
        """Statically infer what a factory function returns (DI wiring pattern).

        All resolvable `return` expressions must agree on one class or library
        client; anything else (or recursion) leaves the type unresolved.
        """
        entry = self._fn_by_id.get(fid)
        if entry is None or depth > 4:
            return None
        fmod, fn = entry
        if fn.inferred_return is not None:
            return fn.inferred_return if fn.inferred_return[0] in ("class", "lib") else None
        fn.inferred_return = ("pending", None)  # recursion guard
        out = None
        for n in ast.walk(fn.node):
            if not isinstance(n, (ast.Return, ast.Yield)) or n.value is None:
                continue
            r = None
            if isinstance(n.value, ast.Call):
                r = self.class_of_call(fmod, n.value, fn, depth + 1)
            elif isinstance(n.value, ast.Name):
                if n.value.id in fn.local_types:
                    r = fn.local_types[n.value.id]
                elif n.value.id in fmod.instances:
                    r = self.class_of_call(fmod, fmod.instances[n.value.id], fn, depth + 1)
                elif n.value.id in fmod.aliases:
                    r = self.resolve_name(fmod, fmod.aliases[n.value.id].split("."), fn)
                    if r and r[0] == "class":
                        r = ("class", r[1])
            if r is None or r[0] not in ("class", "lib"):
                out = ("divergent", None)
                break
            if out is None:
                out = r
            elif out != r:
                out = ("divergent", None)
                break
        fn.inferred_return = out if out and out[0] in ("class", "lib") else ("unknown", None)
        return fn.inferred_return if fn.inferred_return[0] in ("class", "lib") else None

    def _env_var_of(self, mod: ModuleInfo, node, fn: FuncCtx | None):
        """If `node` reads an environment variable, return (VAR, has_default)."""
        if isinstance(node, ast.Subscript):
            base = dotted(node.value)
            if base and self._is_environ(mod, base, fn):
                key = const_str(node.slice)
                if key:
                    return (key, False)
        if isinstance(node, ast.Call):
            parts = dotted(node.func)
            if parts:
                fq = self._lib_fq(mod, parts, fn)
                if fq in ("os.getenv", "os.environ.get") and node.args:
                    key = const_str(node.args[0])
                    if key:
                        return (key, len(node.args) > 1 or any(k.arg == "default" for k in node.keywords))
        return None

    def _is_environ(self, mod: ModuleInfo, parts: list[str], fn: FuncCtx | None = None) -> bool:
        return self._lib_fq(mod, parts, fn) == "os.environ"

    def _lib_fq(self, mod: ModuleInfo, parts: list[str], fn: FuncCtx | None = None) -> str | None:
        head = parts[0]
        source = None
        if fn is not None and head in fn.local_imports:
            source = fn.local_imports[head]
        elif head in mod.bindings:
            source = mod.bindings[head]
        if source and not self._internal_module(source):
            return ".".join([source] + parts[1:])
        return None

    DB_MOCK_NAMES = {
        "sqlite3", "sqlalchemy", "psycopg2", "psycopg", "asyncpg", "pymysql", "mysql",
        "pymongo", "motor", "redis", "get_connection", "connect", "database", "db", "engine", "Session"
    }
    HTTP_MOCK_NAMES = {
        "requests", "httpx", "aiohttp", "urllib", "urllib3", "ClientSession", "AsyncClient", "Client"
    }
    MOCK_CTORS = {"Mock", "MagicMock", "AsyncMock"}

    def _is_mock_ctor(self, mod: ModuleInfo, fn: FuncCtx | None, parts: list[str]) -> bool:
        if not parts:
            return False
        if parts[-1] in self.MOCK_CTORS:
            return True
        if len(parts) >= 2 and parts[-2] in ("mock", "unittest.mock") and parts[-1] in self.MOCK_CTORS:
            return True
        fq = self._lib_fq(mod, parts, fn)
        if fq and any(fq.endswith(f".{m}") or fq == m for m in self.MOCK_CTORS):
            return True
        return False

    def _mock_record(self, mod: ModuleInfo, fn: FuncCtx, call: ast.Call) -> dict | None:
        parts = dotted(call.func) or []
        if not parts:
            return None
        # 1. unittest.mock.patch / patch(...) / patch.object(...)
        if parts[-1] == "patch" or (len(parts) >= 2 and parts[-2] == "patch" and parts[-1] == "object"):
            if parts[-1] == "object" and len(call.args) >= 2:
                obj = dotted(call.args[0])
                name = const_str(call.args[1])
                return {"kind": "attr", "obj": obj, "name": name, "line": call.lineno}
            if call.args:
                tgt = const_str(call.args[0])
                if tgt:
                    return {"kind": "patch", "target": tgt, "line": call.lineno}
        # 2. mocker.patch(...) / mocker.patch.object(...)
        if parts[0] == "mocker" and len(parts) >= 2:
            if parts[1] == "patch":
                if len(parts) >= 3 and parts[2] == "object" and len(call.args) >= 2:
                    obj = dotted(call.args[0])
                    name = const_str(call.args[1])
                    return {"kind": "attr", "obj": obj, "name": name, "line": call.lineno}
                if call.args:
                    tgt = const_str(call.args[0])
                    if tgt:
                        return {"kind": "patch", "target": tgt, "line": call.lineno}
        # 3. monkeypatch.setattr(...)
        if parts[0] == "monkeypatch" and len(parts) >= 2 and parts[1] == "setattr":
            if len(call.args) >= 2:
                tgt = const_str(call.args[0])
                if tgt:
                    return {"kind": "patch", "target": tgt, "line": call.lineno}
                obj = dotted(call.args[0])
                name = const_str(call.args[1])
                return {"kind": "attr", "obj": obj, "name": name, "line": call.lineno}
        return None

    def _is_db_or_http_patch(self, rec: dict) -> str | None:
        """Returns 'database', 'external_api', or None."""
        if rec["kind"] == "patch":
            tgt = rec.get("target") or ""
            parts = tgt.split(".")
            if any(p in self.DB_MOCK_NAMES for p in parts):
                return "database"
            if any(p in self.HTTP_MOCK_NAMES for p in parts):
                return "external_api"
            res = self.resolve_symbol(tgt)
            if res and res[0] in ("func", "func_inferred") and res[1]:
                for e in self.edges.values():
                    if e["source"] == res[1]:
                        if e["type"] == "uses_database":
                            return "database"
                        if e["type"] == "calls_http":
                            return "external_api"
        elif rec["kind"] == "attr":
            obj_parts = rec.get("obj") or []
            name = rec.get("name") or ""
            all_parts = set(obj_parts + [name])
            if any(p in self.DB_MOCK_NAMES for p in all_parts):
                return "database"
            if any(p in self.HTTP_MOCK_NAMES for p in all_parts):
                return "external_api"
        return None

    # ------------------------------------------------------------- application wiring
    def _resolve_app_wiring(self):
        self.include_map: dict[str, dict] = {}
        self.apps: dict[str, dict] = {}
        # apps and routers
        for mod in self.modules.values():
            if mod.is_test:
                continue
            for var, call in mod.instances.items():
                r = self.class_of_call(mod, call, None)
                if not r or r[0] != "lib":
                    continue
                fq = r[1]
                if fq in APP_CTORS:
                    app_id = f"app:{mod.name}.{var}"
                    self.apps[f"{mod.name}.{var}"] = {"id": app_id}
                    mod.routers[var] = {"kind": "app", "framework": APP_CTORS[fq], "prefix": "", "line": call.lineno}
                    self.add_component(app_id, "app", f"{var} ({APP_CTORS[fq]})", mod.rel, call.lineno,
                                       framework=APP_CTORS[fq], module=mod.name)
                    self.add_edge(f"module:{mod.name}", app_id, "contains", "confirmed", self.ev(mod, call.lineno))
                    for kw in call.keywords:
                        if kw.arg == "lifespan":
                            target = self.resolve_name(mod, dotted(kw.value) or ["?"], None)
                            if target and target[0] == "func":
                                self.add_edge(app_id, target[1], "runs_on_startup", "confirmed",
                                              self.ev(mod, call.lineno, "FastAPI lifespan handler"))
                elif fq in ROUTER_CTORS:
                    prefix = ""
                    for kw in call.keywords:
                        if kw.arg in ("prefix", "url_prefix"):
                            prefix = const_str(kw.value) or ""
                    mod.routers[var] = {"kind": "router", "framework": ROUTER_CTORS[fq], "prefix": prefix, "line": call.lineno}
        # include_router / register_blueprint / on_event
        for mod in self.modules.values():
            if mod.is_test:
                continue
            for n in ast.walk(mod.tree):
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and \
                        n.func.attr in ("include_router", "register_blueprint") and n.args:
                    owner = dotted(n.func.value)
                    inc = dotted(n.args[0])
                    if not owner or not inc:
                        continue
                    owner_fq = self._router_fq(mod, owner)
                    inc_fq = self._router_fq(mod, inc)
                    prefix = ""
                    for kw in n.keywords:
                        if kw.arg in ("prefix", "url_prefix"):
                            prefix = const_str(kw.value) or ""
                    if owner_fq and inc_fq:
                        self.include_map[inc_fq] = {"owner": owner_fq, "prefix": prefix, "ev": self.ev(mod, n.lineno, "router included")}
        # routes from decorators
        for mod in self.modules.values():
            if mod.is_test:
                continue
            for fn in mod.functions.values():
                for dec in fn.node.decorator_list:
                    self._route_from_decorator(mod, fn, dec)

        # Django and DRF routes
        for mod in self.modules.values():
            if mod.is_test:
                continue
            # DRF router registrations
            for var, rdata in mod.drf_routers.items():
                for prefix, ref, line in rdata.get("registrations", []):
                    prefix_norm = "/" + prefix.strip("/") if prefix.strip("/") else ""
                    res = self.resolve_name(mod, ref, None)
                    if res and res[0] == "class":
                        cls_fq = res[1]
                        standard_actions = [
                            ("list", "GET", f"{prefix_norm}" or "/"),
                            ("create", "POST", f"{prefix_norm}" or "/"),
                            ("retrieve", "GET", f"{prefix_norm}/{{x}}"),
                            ("update", "PUT", f"{prefix_norm}/{{x}}"),
                            ("partial_update", "PATCH", f"{prefix_norm}/{{x}}"),
                            ("destroy", "DELETE", f"{prefix_norm}/{{x}}"),
                        ]
                        found_any = False
                        for act, method, p in standard_actions:
                            m = self.find_method(cls_fq, act)
                            if m:
                                found_any = True
                                full = norm_path(p)
                                rid = f"route:{method} {full}"
                                self.add_component(rid, "route", f"{method} {full}", mod.rel, line, method=method, path=full,
                                                   framework="django", handler=m)
                                self.add_edge(rid, m, "handled_by", "confirmed", self.ev(mod, line, f"DRF router action '{act}'"))
                                self.frameworks.add("Django")
                        modname, clsname = self._split_class_fq(cls_fq)
                        cmod = self.modules.get(modname)
                        if cmod and clsname in cmod.classes:
                            cnode = cmod.classes[clsname]
                            for item in cnode.body:
                                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                                    for dec in item.decorator_list:
                                        dec_func = dec.func if isinstance(dec, ast.Call) else dec
                                        if (dotted(dec_func) or [""])[-1] == "action":
                                            found_any = True
                                            detail = False
                                            methods = ["GET"]
                                            if isinstance(dec, ast.Call):
                                                for kw in dec.keywords:
                                                    if kw.arg == "detail" and isinstance(kw.value, ast.Constant):
                                                        detail = bool(kw.value.value)
                                                    elif kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
                                                        methods = [str(const_str(e)).upper() for e in kw.value.elts if const_str(e)]
                                            action_path = f"{prefix_norm}/{{x}}/{item.name}" if detail else f"{prefix_norm}/{item.name}"
                                            full = norm_path(action_path)
                                            fid = f"func:{modname}.{clsname}.{item.name}"
                                            for m in methods:
                                                rid = f"route:{m} {full}"
                                                self.add_component(rid, "route", f"{m} {full}", cmod.rel, item.lineno, method=m, path=full,
                                                                   framework="django", handler=fid)
                                                self.add_edge(rid, fid, "handled_by", "confirmed", self.ev(cmod, item.lineno, f"DRF @action '{item.name}'"))
                                                self.frameworks.add("Django")
                        if not found_any:
                            for act, method, p in standard_actions:
                                full = norm_path(p)
                                rid = f"route:{method} {full}"
                                self.add_component(rid, "route", f"{method} {full}", mod.rel, line, method=method, path=full,
                                                   framework="django", handler=f"class:{cls_fq}")
                                self.add_edge(rid, f"class:{cls_fq}", "handled_by", "inferred", self.ev(mod, line, f"DRF router {act}"))
                                self.frameworks.add("Django")

            # Django urlpatterns
            if mod.has_urlpatterns:
                self._resolve_django_urlpatterns(mod)

    def _resolve_django_urlpatterns(self, mod: ModuleInfo):
        for stmt in mod.tree.body:
            assigns = []
            if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
                targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
                for t in targets:
                    if isinstance(t, ast.Name) and t.id == "urlpatterns":
                        assigns.append(stmt.value)
            elif isinstance(stmt, ast.AugAssign):
                if isinstance(stmt.target, ast.Name) and stmt.target.id == "urlpatterns":
                    assigns.append(stmt.value)
            for val in assigns:
                if isinstance(val, ast.List):
                    self._extract_django_paths(mod, val, "")

    def _extract_django_paths(self, mod: ModuleInfo, url_list: ast.List, prefix: str = ""):
        for elt in url_list.elts:
            if not isinstance(elt, ast.Call):
                continue
            func_name = (dotted(elt.func) or [""])[-1]
            if func_name in ("path", "re_path") and elt.args:
                raw_pattern = const_str(elt.args[0]) or ""
                conv = re.sub(r"<[^>]+>", "{x}", raw_pattern)
                full_pattern = norm_path(prefix + "/" + conv)
                if len(elt.args) >= 2:
                    target_node = elt.args[1]
                    if isinstance(target_node, ast.Call):
                        cfunc = (dotted(target_node.func) or [""])[-1]
                        if cfunc == "include" and target_node.args:
                            inc_arg = target_node.args[0]
                            inc_mod_name = const_str(inc_arg)
                            if inc_mod_name and inc_mod_name in self.modules:
                                inc_mod = self.modules[inc_mod_name]
                                for stmt in inc_mod.tree.body:
                                    if isinstance(stmt, ast.Assign):
                                        for t in stmt.targets:
                                            if isinstance(t, ast.Name) and t.id == "urlpatterns" and isinstance(stmt.value, ast.List):
                                                self._extract_django_paths(inc_mod, stmt.value, full_pattern)
                            continue
                        elif cfunc == "as_view":
                            cbv_ref = dotted(target_node.func.value) if isinstance(target_node.func, ast.Attribute) else None
                            if cbv_ref:
                                res = self.resolve_name(mod, cbv_ref, None)
                                if res and res[0] == "class":
                                    cls_fq = res[1]
                                    http_methods_found = []
                                    for verb in ("get", "post", "put", "patch", "delete", "head", "options"):
                                        m = self.find_method(cls_fq, verb)
                                        if m:
                                            http_methods_found.append((verb.upper(), m))
                                    if http_methods_found:
                                        for verb, m in http_methods_found:
                                            rid = f"route:{verb} {full_pattern}"
                                            self.add_component(rid, "route", f"{verb} {full_pattern}", mod.rel, elt.lineno,
                                                               method=verb, path=full_pattern, framework="django", handler=m)
                                            self.add_edge(rid, m, "handled_by", "confirmed", self.ev(mod, elt.lineno, "Django CBV"))
                                            self.frameworks.add("Django")
                                    else:
                                        rid = f"route:GET {full_pattern}"
                                        self.add_component(rid, "route", f"GET {full_pattern}", mod.rel, elt.lineno,
                                                           method="GET", path=full_pattern, framework="django", handler=f"class:{cls_fq}")
                                        self.add_edge(rid, f"class:{cls_fq}", "handled_by", "confirmed", self.ev(mod, elt.lineno, "Django CBV"))
                                        self.frameworks.add("Django")
                            continue
                    view_ref = dotted(target_node)
                    if view_ref:
                        res = self.resolve_name(mod, view_ref, None)
                        if res and res[0] in ("func", "func_inferred"):
                            fid = res[1]
                            rid = f"route:GET {full_pattern}"
                            self.add_component(rid, "route", f"GET {full_pattern}", mod.rel, elt.lineno,
                                               method="GET", path=full_pattern, framework="django", handler=fid)
                            self.add_edge(rid, fid, "handled_by", "confirmed", self.ev(mod, elt.lineno, "Django path"))
                            self.frameworks.add("Django")

    def _router_fq(self, mod: ModuleInfo, parts: list[str]) -> str | None:
        if len(parts) == 1 and parts[0] in mod.routers:
            return f"{mod.name}.{parts[0]}"
        if parts[0] in mod.bindings:
            fq = ".".join([mod.bindings[parts[0]]] + parts[1:])
            modname, _, var = fq.rpartition(".")
            if modname in self.modules and var in self.modules[modname].routers:
                return fq
            if fq in self.modules:  # e.g. `from app.routes import products` then products.router
                return None
        return None

    def _mount(self, router_fq: str) -> tuple[str, str | None, dict | None]:
        """Follow include_router chain -> (accumulated prefix, app fq, evidence)."""
        prefix, ev, seen = "", None, set()
        cur = router_fq
        while cur not in self.apps and cur in self.include_map and cur not in seen:
            seen.add(cur)
            inc = self.include_map[cur]
            prefix = inc["prefix"] + prefix
            ev = ev or inc["ev"]
            cur = inc["owner"]
        return prefix, (cur if cur in self.apps else None), ev

    def _route_from_decorator(self, mod: ModuleInfo, fn: FuncCtx, dec):
        if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)):
            return
        attr = dec.func.attr
        owner = dotted(dec.func.value)
        if not owner:
            return
        router_fq = self._router_fq(mod, owner)
        if attr == "on_event" and router_fq in self.apps:
            if const_str(dec.args[0] if dec.args else None) == "startup":
                self.add_edge(self.apps[router_fq]["id"], fn.id, "runs_on_startup", "confirmed",
                              self.ev(mod, dec.lineno, "startup event handler"))
            return
        if attr not in ROUTE_DECORATORS or router_fq is None:
            return
        rmod_name, _, rvar = router_fq.rpartition(".")
        router = self.modules[rmod_name].routers[rvar]
        path = const_str(dec.args[0]) if dec.args else None
        for kw in dec.keywords:
            if kw.arg == "path":
                path = const_str(kw.value)
        if path is None:
            return
        methods = [attr.upper()] if attr in HTTP_VERBS else []
        if attr in ("route", "api_route"):
            for kw in dec.keywords:
                if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
                    methods = [str(const_str(e)).upper() for e in kw.value.elts if const_str(e)]
            methods = methods or ["GET"]
        if attr == "websocket":
            methods = ["WS"]
        include_prefix, app_fq, inc_ev = ("", router_fq, None) if router_fq in self.apps else self._mount(router_fq)
        full = norm_path(include_prefix + router["prefix"] + path)
        for m in methods:
            rid = f"route:{m} {full}"
            self.add_component(rid, "route", f"{m} {full}", mod.rel, dec.lineno, method=m, path=full,
                               framework=router["framework"], handler=fn.id,
                               is_async=isinstance(fn.node, ast.AsyncFunctionDef))
            self.add_edge(rid, fn.id, "handled_by", "confirmed", self.ev(mod, dec.lineno, f"@{'.'.join(owner)}.{attr} decorator"))
            if app_fq:
                cls = "confirmed" if (router_fq in self.apps or inc_ev) else "inferred"
                self.add_edge(rid, self.apps[app_fq]["id"], "served_by", cls,
                              inc_ev or self.ev(mod, dec.lineno, "route registered on application"))
            elif len(self.apps) == 1:
                only = next(iter(self.apps.values()))["id"]
                self.add_edge(rid, only, "served_by", "inferred", self.ev(mod, dec.lineno, "single application in project; router inclusion not statically resolved"))
        # FastAPI Depends(...) in the handler signature
        defaults = list(fn.node.args.defaults) + [d for d in fn.node.args.kw_defaults if d is not None]
        for d in defaults:
            if isinstance(d, ast.Call) and (dotted(d.func) or [""])[-1] == "Depends" and d.args:
                r = self.resolve_name(mod, dotted(d.args[0]) or ["?"], None)
                if r and r[0] in ("func", "func_inferred"):
                    self.add_edge(fn.id, r[1], "calls", "inferred", self.ev(mod, fn.node.lineno, "FastAPI Depends()"))

    # ------------------------------------------------------------- function bodies
    def _collect(self, mod: ModuleInfo, fn: FuncCtx):
        """Walk the function body, tracking enclosing try/except handlers for each call."""
        if fn.collected:
            return
        fn.collected = True
        # closures see the enclosing functions' imports and nested defs
        parent = mod.functions.get(fn.parent) if fn.parent else None
        hops = 0
        while parent is not None and hops < 8:
            for k, v in parent.local_defs.items():
                fn.local_defs.setdefault(k, v)
            for k, v in parent.local_imports.items():
                fn.local_imports.setdefault(k, v)
            parent = mod.functions.get(parent.parent) if parent.parent else None
            hops += 1
        root = fn.node

        def walk(node, tries):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.asname:
                        fn.local_imports.setdefault(a.asname, a.name)
                    else:
                        top = a.name.split(".")[0]
                        fn.local_imports.setdefault(top, top)
                    self._note_import(mod, a.name, node.lineno)
                return
            if isinstance(node, ast.ImportFrom):
                base = self._resolve_relative(mod, node.level, node.module) if node.level else (node.module or "")
                for a in node.names:
                    if a.name != "*":
                        fn.local_imports.setdefault(a.asname or a.name, f"{base}.{a.name}" if base else a.name)
                self._note_import(mod, base, node.lineno)
                return
            if node is not root and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                # nested def: recorded in this scope; its own body is analysed separately
                nested_qual = f"{fn.qual}.<locals>.{node.name}"
                kind = "class" if isinstance(node, ast.ClassDef) else "func"
                prefix = f"{mod.name}.{nested_qual}" if kind == "class" else f"func:{mod.name}.{nested_qual}"
                fn.local_defs.setdefault(node.name, (kind, prefix))
                return
            if isinstance(node, ast.Lambda):
                return
            if isinstance(node, (ast.Try, getattr(ast, "TryStar", ast.Try))):
                handlers = [{"names": handler_names(h), "reraises": has_raise(h.body), "line": h.lineno} for h in node.handlers]
                for h in node.handlers:
                    names = handler_names(h)
                    swallow = all(isinstance(s, (ast.Pass, ast.Expr, ast.Continue)) for s in h.body)
                    if ("*" in names or "Exception" in names or "BaseException" in names) and swallow and not has_raise(h.body):
                        self.broad_excepts.append({"component": fn.id, **self.ev(mod, h.lineno)})
                for s in node.body:
                    walk(s, tries + [handlers])
                for h in node.handlers:
                    for s in h.body:
                        walk(s, tries)
                for s in node.orelse + node.finalbody:
                    walk(s, tries)
                return
            if isinstance(node, ast.Call):
                fn.calls.append((node, tries))
                var = self._env_var_of(mod, node, fn)
                if var:
                    fn.env_reads.append((var[0], node.lineno, var[1], tries))
                rec = self._mock_record(mod, fn, node)
                if rec:
                    fn.patch_targets.append(rec)
            elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load):
                var = self._env_var_of(mod, node, fn)
                if var:
                    fn.env_reads.append((var[0], node.lineno, var[1], tries))
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for t in targets:
                    if isinstance(t, ast.Name) and node.value is not None:
                        if isinstance(node.value, ast.Call):
                            r = self.class_of_call(mod, node.value, fn)
                            if r:
                                fn.local_types[t.id] = r
                        var = self._env_var_of(mod, node.value, fn)
                        if var:
                            fn.local_env[t.id] = var[0]
                    if isinstance(t, ast.Attribute) and isinstance(node.value, ast.Call) and \
                            self._is_mock_ctor(mod, fn, dotted(node.value.func) or []):
                        obj = dotted(t.value)
                        if obj:  # e.g. db.get_connection = MagicMock()
                            fn.patch_targets.append({"kind": "attr", "obj": obj, "name": t.attr, "line": node.lineno})
            if isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    if isinstance(item.optional_vars, ast.Name) and isinstance(item.context_expr, ast.Call):
                        r = self.class_of_call(mod, item.context_expr, fn)
                        if r:
                            fn.local_types[item.optional_vars.id] = r
            for child in ast.iter_child_nodes(node):
                walk(child, tries)

        for dec in fn.node.decorator_list:
            if isinstance(dec, ast.Call):
                rec = self._mock_record(mod, fn, dec)
                if rec:
                    fn.patch_targets.append(rec)
        for stmt in root.body:
            walk(stmt, [])
        fn.raises = raised_names(root.body)

    def _analyse_function(self, mod: ModuleInfo, fn: FuncCtx):
        self._collect(mod, fn)
        defaults = list(fn.node.args.defaults) + [d for d in fn.node.args.kw_defaults if d is not None]
        args = [a.arg for a in fn.node.args.args if a.arg not in ("self", "cls")]
        for arg, default in zip(args[-len(defaults):], defaults):
            if default is not None:
                r = None
                if isinstance(default, ast.Call):
                    if (dotted(default.func) or [""])[-1] == "Depends" and default.args:
                        dparts = dotted(default.args[0])
                        if dparts:
                            dres = self.resolve_name(mod, dparts, fn)
                            if dres and dres[0] in ("func", "func_inferred"):
                                r = self._infer_factory_return(mod, dres[1])
                            elif dres and dres[0] == "class":
                                r = ("class", dres[1])
                    else:
                        r = self.class_of_call(mod, default, fn)
                if r and r[0] in ("class", "lib"):
                    fn.local_types[arg] = r
        comp = self.components[fn.id]
        comp["data"]["raises"] = sorted(fn.raises)
        sql = []
        for call, tries in fn.calls:
            self._handle_call(mod, fn, call, tries)
            if isinstance(call.func, ast.Attribute) and call.func.attr in ("execute", "executemany", "executescript") and call.args:
                s = const_str(call.args[0])
                if s and SQL_RE.match(s):
                    sql.append({"line": call.lineno, "statement": " ".join(s.split())[:160]})
        if sql:
            comp["data"]["sql"] = sql
        for var, line, has_default, tries in fn.env_reads:
            env_id = f"env:{var}"
            self.add_component(env_id, "env_var", var, None, None)
            guard = tries + ([[{"names": ["<default>"], "reraises": False, "line": line}]] if has_default else [])
            self.add_edge(fn.id, env_id, "reads_env", "confirmed",
                          self.ev(mod, line, "has default value" if has_default else "no default: missing value raises KeyError",
                                  has_default=has_default), guard)
            if not has_default:
                self.env_required.append({"var": var, "component": fn.id, **self.ev(mod, line)})
        # worker detection: classes whose methods start threads/processes
        if fn.cls:
            for call, _ in fn.calls:
                r = self.class_of_call(mod, call, fn)
                if r and r[0] == "lib" and r[1] in THREAD_CTORS:
                    cls_fq = f"{mod.name}.{fn.cls}"
                    wid = f"worker:{cls_fq}"
                    w = self.add_component(wid, "worker", f"{fn.cls} (background worker)", mod.rel,
                                           mod.classes[fn.cls].lineno, module=mod.name, cls=fn.cls, kind=r[1])
                    launchers = set(w["data"].get("launcher_methods", [])) | {fn.qual.split(".")[-1]}
                    w["data"]["launcher_methods"] = sorted(launchers)
                    self.add_edge(f"class:{cls_fq}", wid, "runs_worker", "confirmed", self.ev(mod, call.lineno, f"starts {r[1]}"))

    def _handle_call(self, mod: ModuleInfo, fn: FuncCtx, call: ast.Call, tries):
        parts = dotted(call.func)
        res = None
        if parts:
            if parts[0] == "self" and fn.cls:
                cls_fq = f"{mod.name}.{fn.cls}"
                if len(parts) == 2:
                    m = self.find_method(cls_fq, parts[1])
                    res = ("func", m) if m else None
                elif len(parts) == 3:
                    t = mod.class_attr_types.get(fn.cls, {}).get(parts[1])
                    if t:
                        r = self.class_of_call(mod, t[1], None)
                        if r and r[0] == "class":
                            m = self.find_method(r[1], parts[2])
                            res = ("func_inferred", m) if m else None
                        elif r and r[0] == "lib":
                            res = ("lib_method", r[1], parts[2])
            else:
                res = self.resolve_name(mod, parts, fn)
        elif isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Call):
            r = self.class_of_call(mod, call.func.value, fn)
            if r and r[0] == "class":
                m = self.find_method(r[1], call.func.attr)
                res = ("func_inferred", m) if m else None
            elif r and r[0] == "lib":
                res = ("lib_method", r[1], call.func.attr)

        if res is None:
            if parts and len(parts) == 1 and parts[0] not in BUILTIN_NAMES:
                self.stats["unresolved_calls"] += 1
            return
        kind = res[0]
        ev = self.ev(mod, call.lineno)
        if kind in ("func", "func_inferred") and res[1]:
            self.stats["resolved_calls"] += 1
            self.add_edge(fn.id, res[1], "calls", "confirmed" if kind == "func" else "inferred", ev, tries)
        elif kind == "class":
            self.stats["resolved_calls"] += 1
            init = self.find_method(res[1], "__init__")
            if init:
                self.add_edge(fn.id, init, "calls", "confirmed", {**ev, "detail": "constructor call"}, tries)
            elif f"class:{res[1]}" in self.components:
                self.add_edge(fn.id, f"class:{res[1]}", "calls", "confirmed", {**ev, "detail": "class instantiation"}, tries)
        elif kind == "lib":
            self.stats["library_calls"] += 1
            self._lib_call(mod, fn, call, res[1], tries, "confirmed")
        elif kind == "lib_method":
            self.stats["library_calls"] += 1
            base, method = res[1], res[2]
            if base in HTTP_CLIENT_CTORS and method in HTTP_VERBS | {"request", "stream", "send"}:
                self._http_call(mod, fn, call, f"{base}.{method}", tries, "inferred", client_ctor=None)
            elif base in DB_CALLS:
                engine = DB_CALLS[base]
                db_id = f"database:{engine}"
                self.add_component(db_id, "database", f"{engine} database", None, None, engine=engine,
                                   libraries=sorted(set(self.components.get(db_id, {}).get("data", {}).get("libraries", [])) | {base.split('.')[0]}))
                self.add_edge(fn.id, db_id, "uses_database", "inferred", self.ev(mod, call.lineno, f"{base}.{method}()"), tries)

    def _lib_call(self, mod, fn, call, fq, tries, classification):
        if fq in DB_CALLS:
            engine = DB_CALLS[fq]
            db_id = f"database:{engine}"
            self.add_component(db_id, "database", f"{engine} database", None, None, engine=engine,
                               libraries=sorted(set(self.components.get(db_id, {}).get("data", {}).get("libraries", [])) | {fq.split('.')[0]}))
            self.add_edge(fn.id, db_id, "uses_database", classification, self.ev(mod, call.lineno, f"{fq}()"), tries)
        elif fq in HTTP_FUNCS:
            self._http_call(mod, fn, call, fq, tries, classification)

    def _http_call(self, mod, fn, call, fq, tries, classification, client_ctor=None):
        label, key = self._url_hint(mod, fn, call)
        ext_id = f"external:{key}"
        self.add_component(ext_id, "external_api", label, None, None, hint=key)
        self.add_edge(fn.id, ext_id, "calls_http", classification, self.ev(mod, call.lineno, f"{fq}()"), tries)
        has_timeout = any(k.arg == "timeout" for k in call.keywords)
        client_timeout = False
        if fn.cls and isinstance(call.func, ast.Attribute):
            attr_parts = dotted(call.func.value) or []
            if len(attr_parts) == 2 and attr_parts[0] == "self":
                t = mod.class_attr_types.get(fn.cls, {}).get(attr_parts[1])
                if t and any(k.arg == "timeout" for k in t[1].keywords):
                    client_timeout = True
        self.http_findings.append({"component": fn.id, "external": ext_id, "lib": fq, "timeout": has_timeout or client_timeout,
                                   "guarded": bool(tries), **self.ev(mod, call.lineno)})

    def _url_hint(self, mod: ModuleInfo, fn: FuncCtx, call: ast.Call) -> tuple[str, str]:
        url_node = call.args[0] if call.args else next((k.value for k in call.keywords if k.arg == "url"), None)
        if url_node is not None:
            for n in ast.walk(url_node):
                if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "self" and fn.cls:
                    var = mod.class_attr_env.get(fn.cls, {}).get(n.attr)
                    if var:
                        return f"External API ({var})", var
                if isinstance(n, ast.Name):
                    var = fn.local_env.get(n.id) or mod.module_env.get(n.id)
                    if var:
                        return f"External API ({var})", var
                s = const_str(n)
                if s and s.startswith(("http://", "https://")):
                    host = urlparse(s).hostname or "unknown-host"
                    return f"External API ({host})", host
        return f"External HTTP service ({mod.name})", f"unresolved.{mod.name}"

    # ------------------------------------------------------------- tests
    def _analyse_tests(self):
        fixtures: dict[str, list] = {}  # name -> [(mod, FuncCtx)]
        tests: list[tuple[ModuleInfo, FuncCtx]] = []
        for mod in self.modules.values():
            if not mod.is_test:
                continue
            for qual, fn in mod.functions.items():
                name = qual.split(".")[-1]
                is_fixture = any((dotted(d.func if isinstance(d, ast.Call) else d) or [""])[-1] == "fixture"
                                 for d in fn.node.decorator_list)
                if is_fixture:
                    fixtures.setdefault(name, []).append((mod, fn))
                elif name.startswith("test") and (fn.cls is None or fn.cls.startswith("Test")):
                    tests.append((mod, fn))
        routes = [c for c in self.components.values() if c["type"] == "route"]
        cache: dict[str, list] = {}

        def targets_of(mod: ModuleInfo, fn: FuncCtx) -> list:
            if fn.id in cache:
                return cache[fn.id]
            cache[fn.id] = []
            self._collect(mod, fn)
            out = []
            for call, _ in fn.calls:
                if isinstance(call.func, ast.Attribute) and call.func.attr in HTTP_VERBS and call.args:
                    tpl = path_template(call.args[0])
                    if tpl and tpl.startswith("/"):
                        method = call.func.attr.upper()
                        for r in routes:
                            if r["data"]["method"] == method and paths_match(r["data"]["path"], tpl):
                                out.append((r["id"], "exercises", "inferred", self.ev(mod, call.lineno, f"test client {method} {tpl}")))
                        continue
                parts = dotted(call.func)
                res = self.resolve_name(mod, parts, fn) if parts else None
                if res is None and isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Call):
                    r = self.class_of_call(mod, call.func.value, fn)
                    if r and r[0] == "class":
                        m = self.find_method(r[1], call.func.attr)
                        res = ("func_inferred", m) if m else None
                if res and res[0] in ("func", "func_inferred") and res[1] and res[1] in self.components:
                    out.append((res[1], "calls", "confirmed" if res[0] == "func" else "inferred", self.ev(mod, call.lineno)))
                elif res and res[0] == "class":
                    init = self.find_method(res[1], "__init__")
                    if init:
                        out.append((init, "calls", "confirmed", self.ev(mod, call.lineno, "constructor call")))
                    elif f"class:{res[1]}" in self.components:
                        out.append((f"class:{res[1]}", "calls", "confirmed", self.ev(mod, call.lineno, "class instantiation")))
            params = [a.arg for a in fn.node.args.args if a.arg not in ("self", "cls")]
            autouse = [n for n, defs in fixtures.items() for (_, f) in defs
                       if any(isinstance(d, ast.Call) and any(k.arg == "autouse" and getattr(k.value, "value", False) for k in d.keywords)
                              for d in f.node.decorator_list)]
            for p in dict.fromkeys(params + autouse):
                for fmod, ffn in fixtures.get(p, []):
                    if ffn is fn:
                        continue
                    for tgt, et, _, ev in targets_of(fmod, ffn):
                        out.append((tgt, et, "inferred", {**ev, "detail": f"via fixture '{p}': {ev.get('detail') or 'call'}"}))
            cache[fn.id] = out
            return out

        for mod, fn in tests:
            nodeid = f"{mod.rel}::{fn.qual.replace('.', '::')}"
            classname = mod.rel[:-3].replace("/", ".") + (f".{fn.cls}" if fn.cls else "")
            tid = f"test:{nodeid}"

            self._collect(mod, fn)
            all_patches = list(fn.patch_targets)
            params = [a.arg for a in fn.node.args.args if a.arg not in ("self", "cls")]
            for p in dict.fromkeys(params):
                for fmod, ffn in fixtures.get(p, []):
                    self._collect(fmod, ffn)
                    all_patches.extend(ffn.patch_targets)

            mock_types = set()
            mock_targets = []
            for p in all_patches:
                mtype = self._is_db_or_http_patch(p)
                if mtype:
                    mock_types.add(mtype)
                    tgt_name = p.get("target") or ".".join((p.get("obj") or []) + [p.get("name", "")])
                    mock_targets.append(tgt_name)

            is_mocked = bool(mock_types)
            self.add_component(tid, "test", fn.qual, mod.rel, fn.node.lineno, nodeid=nodeid,
                               junit_key=f"{classname}::{fn.node.name}",
                               mocked=is_mocked, mock_types=sorted(mock_types), mocks=mock_targets)
            for tgt, et, cls, ev in targets_of(mod, fn):
                self.add_edge(tid, tgt, et, cls, ev, mocked=is_mocked)

    # ------------------------------------------------------------- manifests
    def _read_manifests(self, manifests: list[str]) -> list[dict]:
        deps = []
        for rel in manifests:
            path = self.root / rel
            name = path.name
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if name == "requirements.txt":
                for line in text.splitlines():
                    line = line.split("#")[0].strip()
                    if line and not line.startswith("-"):
                        pkg = re.split(r"[<>=!~\[; ]", line, maxsplit=1)[0]
                        deps.append({"name": pkg, "spec": line[len(pkg):].strip(), "manifest": rel})
            elif name == "pyproject.toml":
                try:
                    data = tomllib.loads(text)
                except tomllib.TOMLDecodeError:
                    continue
                for line in data.get("project", {}).get("dependencies", []):
                    pkg = re.split(r"[<>=!~\[; ]", line, maxsplit=1)[0]
                    deps.append({"name": pkg, "spec": line[len(pkg):].strip(), "manifest": rel})
                for pkg, spec in data.get("tool", {}).get("poetry", {}).get("dependencies", {}).items():
                    deps.append({"name": pkg, "spec": str(spec), "manifest": rel})
            elif name == "package.json":
                import json
                try:
                    data = json.loads(text)
                except ValueError:
                    continue
                for section in ("dependencies", "devDependencies"):
                    for pkg, spec in (data.get(section) or {}).items():
                        deps.append({"name": pkg, "spec": spec, "manifest": rel})
        return deps


def analyse_project(root: Path, progress=None, tree: dict | None = None,
                    cache_dir: Path | None = None, use_cache: bool = True) -> dict:
    cache_dir = cache_dir if cache_dir is not None else default_cache_dir(root)
    cache = FileCache(Path(cache_dir) / "py" if cache_dir else None, use_cache=use_cache, fmt="pickle")
    return Analyzer(root, tree=tree, cache=cache).run(progress)
