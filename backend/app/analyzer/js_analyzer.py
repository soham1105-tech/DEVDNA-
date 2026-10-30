"""Static architecture discovery for JavaScript/TypeScript projects using tree-sitter.

Produces exactly the same component/edge format as `python_analyzer.py`, so the
graph engine, findings and frontend work unchanged:

  components: {"id", "type", "name", "file", "line", "data"}
  edges:      {"id": "<type>|<source>|<target>", "source", "target", "type",
               "classification", "evidence": [...], "call_sites": [...]}

Every edge carries evidence (file, line, redacted snippet) and a classification:
confirmed (directly visible in the source) or inferred (heuristic).

Covered constructs:
  * Express / Fastify / Koa applications, routers and routes, including
    `app.use(prefix, router)` mounting, `express.Router({prefix})` and the
    Fastify plugin convention `export default async function (fastify) {...}`
    registered via `app.register(plugin, {prefix})`.
  * Imports (ESM `import`, CommonJS `require` incl. destructuring) -> module
    `imports` edges and cross-module `calls` resolution.
  * Function and class-method calls (including `this.method()`, instances
    created with `new`, and default-export object method calls).
  * HTTP clients: global / node-fetch `fetch`, `axios` (module and `.create()`
    instances), `got`, and node core `http`/`https` -> `external_api`
    components + `calls_http` edges.
  * Databases: `pg` Pool/Client, `mongoose`, `mysql2`, `redis`, `better-sqlite3`
    -> `database` components + `uses_database` edges.
  * `process.env` reads -> `env_var` components + `reads_env` edges.
  * Jest/Mocha tests (`test()`/`it()`) incl. supertest `request(app).get(...)`
    and same-origin `fetch` URLs matched to routes -> `exercises` edges.

Incremental analysis: per-file extraction models are cached by content hash
(see `cache.py`); unchanged files are not re-parsed on later runs. The
resolution pass always re-runs, because cross-file wiring can change without a
file's own content changing.

Not modelled (same limits as the Python analyzer, plus JS-specific ones):
dynamic dispatch, monkey-patching, `Proxy`, runtime wiring, barrel re-exports,
and destructuring of `process.env`. Calls inside inline callbacks (`.then`,
array combinators, route handlers) are attributed to the enclosing named
function - in JS the callback body *is* the function's logic.
"""
from __future__ import annotations

import json
import logging
import posixpath
import re
from pathlib import Path
from urllib.parse import urlparse

from app.analyzer.cache import FileCache, default_cache_dir
from app.analyzer.ingest import scan_tree
from app.analyzer.redact import find_secrets, redact

log = logging.getLogger("devdna.jsanalyzer")

JS_LANGS = {".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".jsx": "javascript",
            ".ts": "typescript", ".tsx": "tsx"}
RESOLVE_EXTS = [".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"]
HTTP_VERBS = {"get", "post", "put", "patch", "delete", "options", "head"}
ROUTE_METHODS = HTTP_VERBS | {"all"}
HANDLER_METHODS = ROUTE_METHODS  # methods that can register a route handler

FRAMEWORK_IMPORTS = {
    "express": "Express", "fastify": "Fastify", "koa": "Koa", "@koa/router": "Koa", "koa-router": "Koa",
    "next": "Next.js", "react": "React", "vue": "Vue", "jest": "Jest", "mocha": "Mocha", "vitest": "Vitest",
    "supertest": "Supertest", "mongoose": "Mongoose", "sequelize": "Sequelize", "knex": "Knex",
    "nuxt": "Nuxt", "svelte": "Svelte", "@angular/core": "Angular",
}
APP_CTOR_LIBS = {"express": "express", "fastify": "fastify", "koa": "koa", "polka": "polka"}
ROUTER_CTOR_LIBS = {"express.Router": "express", "@koa/router.Router": "koa", "koa-router.Router": "koa"}

DB_LIB_CALLS = {
    "mongoose.connect": "mongodb", "mongoose.createConnection": "mongodb",
    "mysql2.createPool": "mysql", "mysql2.createConnection": "mysql",
    "redis.createClient": "redis", "knex": "knex", "sequelize": "sequelize",
}
DB_CLASS_CTORS = {
    "pg.Pool": "postgresql", "pg.Client": "postgresql",
    "better-sqlite3.Database": "sqlite", "sqlite3.Database": "sqlite",
    "ioredis.Redis": "redis", "redis.Redis": "redis", "redis.Cluster": "redis",
}
DB_INSTANCE_METHODS = {
    "query", "execute", "exec", "all", "run", "get", "connect", "end", "raw",
    "find", "findOne", "findById", "insertOne", "insertMany", "updateOne", "deleteOne",
    "aggregate", "collection", "model", "set", "del", "exists", "expire",
    "hset", "hget", "hgetall", "lpush", "rpush", "publish", "subscribe", "sadd", "smembers", "zadd", "incr",
    "beginTransaction", "commit", "rollback",
}

FETCH_PACKAGES = {"node-fetch", "isomorphic-fetch", "cross-fetch", "undici"}
AXIOS_PACKAGES = {"axios"}
GOT_PACKAGES = {"got"}
NODE_HTTP_PACKAGES = {"http", "https"}

JS_GLOBALS = {
    "console", "process", "module", "exports", "require", "globalThis", "window", "document",
    "JSON", "Math", "Promise", "Object", "Array", "String", "Number", "Boolean", "Date", "RegExp",
    "Error", "TypeError", "RangeError", "SyntaxError", "Buffer", "URL", "URLSearchParams",
    "AbortController", "setTimeout", "setInterval", "setImmediate", "clearTimeout", "clearInterval",
    "queueMicrotask", "structuredClone", "crypto", "performance", "navigator", "localStorage",
    "fetch", "alert", "describe", "it", "test", "beforeEach", "afterEach", "beforeAll", "afterAll",
    "expect", "jest", "vi",
}

_PARSERS: dict[str, object] = {}


def _parser(lang: str):
    if lang not in _PARSERS:
        try:
            from tree_sitter_language_pack import get_parser
        except ImportError as exc:  # pragma: no cover - environment issue
            raise RuntimeError("JS/TS analysis requires the 'tree-sitter-language-pack' package") from exc
        _PARSERS[lang] = get_parser(lang)
    return _PARSERS[lang]


# --------------------------------------------------------------------------- path/module helpers (same semantics as python_analyzer)
def norm_path(p: str) -> str:
    p = p.split("?")[0]
    p = re.sub(r":([A-Za-z0-9_]+)", r"{\1}", p)  # express :param -> {param}
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


def module_name_of(rel: str) -> str:
    """src/routes/products.js -> routes.products; src/index.js -> index; server.js -> server."""
    parts = rel.split("/")
    if parts[0] == "src" and len(parts) > 1:
        parts = parts[1:]
    stem = parts[-1]
    for ext in JS_LANGS:
        if stem.endswith(ext):
            stem = stem[: -len(ext)]
            break
    parts = parts[:-1] + ([stem] if stem != "index" else [])
    return ".".join(parts) or "__root__"


def is_test_file(rel: str) -> bool:
    name = rel.split("/")[-1].lower()
    if ".test." in name or ".spec." in name:
        return True
    return any(part in ("test", "tests", "__tests__") for part in rel.split("/")[:-1])


def resolve_import(from_rel: str, spec: str, files: set[str]) -> str | None:
    """Resolve a relative import specifier to a repository-relative file path."""
    if not spec.startswith("."):
        return None
    base = posixpath.normpath(posixpath.join(posixpath.dirname(from_rel), spec))
    candidates = [base]
    if base.endswith(".js"):  # TS convention: './x.js' may refer to x.ts
        candidates += [base[:-3] + ".ts", base[:-3] + ".tsx"]
    candidates += [base + ext for ext in RESOLVE_EXTS]
    candidates += [posixpath.join(base, "index" + ext) for ext in RESOLVE_EXTS]
    return next((c for c in candidates if c in files), None)


def _real_args(args_node) -> list:
    if args_node is None:
        return []
    return [a for a in args_node.children if a.type not in (",", "(", ")")]


# --------------------------------------------------------------------------- per-file extraction (cacheable)
class _Extractor:
    """Parses one JS/TS file into a JSON-serialisable model. No cross-file logic.

    Records are pushed onto stacks while walking:
      fn_stack  - function-like records (have local_consts/local_new semantics)
      rec_stack - call-attribution records (functions, class methods, test cases)
    """

    def __init__(self, rel: str, source: str, lang: str):
        self.rel = rel
        self.source = source
        self.data = source.encode("utf-8", errors="replace")
        self.lines = source.splitlines()
        self.tree = _parser(lang).parse(self.data)
        self.model: dict = {
            "rel": rel, "module": module_name_of(rel), "is_test": is_test_file(rel), "lang": lang,
            "imports": [], "exports": {}, "apps": {}, "routers": {},
            "mounts": [], "routes": [], "functions": [], "classes": [],
            "instances": {}, "module_consts": {}, "class_attr_env": {}, "tests": [], "secret_hits": [],
            "parse_error": None,
        }
        self.bindings: dict[str, dict] = {}   # local -> {"source", "imported", "kind"}
        self.fn_stack: list[dict] = []
        self.rec_stack: list[dict] = []
        self.plugin_param: str | None = None
        self._walked_fns: set[int] = set()

    # -- syntax helpers -----------------------------------------------------
    def text(self, node) -> str:
        return self.data[node.start_byte:node.end_byte].decode("utf-8", "replace")

    def line(self, node) -> int:
        return node.start_point[0] + 1

    def snippet(self, line: int | None) -> str:
        if not line or line > len(self.lines):
            return ""
        text, _ = redact(self.lines[line - 1].strip())
        return text[:220]

    def children(self, node, *types):
        out = list(node.children) if node is not None else []
        return [c for c in out if c.type in types] if types else out

    def field(self, node, name):
        return node.child_by_field_name(name) if node is not None else None

    def parts(self, node) -> list[str] | None:
        """member/identifier chain -> ['a','b']; subscript -> ['a','[]']; this -> ['this']."""
        if node is None:
            return None
        if node.type in ("identifier", "property_identifier", "this"):
            return [self.text(node)]
        if node.type == "member_expression":
            base = self.parts(self.field(node, "object"))
            prop = self.field(node, "property")
            return base + [self.text(prop)] if base and prop is not None else None
        if node.type == "subscript_expression":
            base = self.parts(self.field(node, "object"))
            return base + ["[]"] if base else None
        return None

    def string_value(self, node) -> str | None:
        if node is not None and node.type == "string":
            return "".join(self.text(c) for c in node.children if c.type == "string_fragment")
        return None

    def path_template(self, node) -> str | None:
        """String / template literal / string concat -> template with {x} placeholders."""
        if node is None:
            return None
        s = self.string_value(node)
        if s is not None:
            return s
        if node.type == "template_string":
            out = []
            for c in node.children:
                if c.type == "string_fragment":
                    out.append(self.text(c))
                elif c.type == "template_substitution":
                    out.append("{x}")
            return "".join(out)
        if node.type == "binary_expression" and self.text(self.field(node, "operator")) == "+":
            left = self.path_template(self.field(node, "left"))
            right = self.path_template(self.field(node, "right"))
            if left is not None and right is not None:
                return left + right
        return None

    # -- entry point ---------------------------------------------------------
    def extract(self) -> dict:
        for hit in find_secrets(self.source):
            self.model["secret_hits"].append({"line": hit["line"], "snippet": self.snippet(hit["line"])})
        if self.tree.root_node.has_error:
            self.model["parse_error"] = {"line": self._first_error_line(self.tree.root_node), "error": "syntax error"}
        for stmt in self.children(self.tree.root_node):
            self.stmt(stmt, [])
        return self.model

    def _first_error_line(self, node) -> int:
        stack, best = [node], None
        while stack:
            n = stack.pop()
            if n.type == "ERROR" or n.is_missing:
                ln = n.start_point[0] + 1
                best = ln if best is None else min(best, ln)
            stack.extend(n.children)
        return best or 1

    # -- statements ----------------------------------------------------------
    # anonymous keyword/punctuation tokens that carry no statement of their own
    _KEYWORD_TOKENS = {"comment", "return", "throw", "else", "if", "for", "while", "do", "switch",
                       "try", "catch", "finally", "case", "default", "break", "continue", "const",
                       "let", "var", "function", "class", "export", "import", ";", "{", "}", "(", ")"}

    def stmt(self, node, tries):
        t = node.type if node is not None else ""
        if t == "import_statement":
            self._import(node)
        elif t == "export_statement":
            self._export(node, tries)
        elif t == "function_declaration":
            self._function_decl(node, exported=False)
        elif t == "class_declaration":
            self._class_decl(node, exported=False, default=False)
        elif t in ("lexical_declaration", "variable_declaration"):
            for d in self.children(node, "variable_declarator"):
                self._declarator(d, tries, top=not self.fn_stack)
        elif t == "expression_statement":
            exprs = self.children(node)
            if exprs:
                self.expr(exprs[0], tries)
        elif t == "try_statement":
            self._try(node, tries)
        elif t in ("if_statement", "for_statement", "for_in_statement", "for_of_statement", "while_statement",
                   "do_statement", "switch_statement", "with_statement", "labeled_statement", "block",
                   "case_statement", "switch_body", "statement_block"):
            for c in self.children(node):
                if c.type in self._KEYWORD_TOKENS:
                    continue
                self.stmt_or_expr(c, tries)
        elif t.endswith("_statement") or t.endswith("_declaration"):
            # return/throw/break/... : walk the returned/thrown expression
            for c in self.children(node):
                if c.type in self._KEYWORD_TOKENS:
                    continue
                self.stmt_or_expr(c, tries)

    def stmt_or_expr(self, node, tries):
        t = node.type
        if t in ("lexical_declaration", "variable_declaration"):
            for d in self.children(node, "variable_declarator"):
                self._declarator(d, tries, top=False)
        elif t in ("statement_block", "switch_body", "case_statement"):
            self.stmt(node, tries)
        elif t.endswith("_statement") or t.endswith("_declaration"):
            self.stmt(node, tries)
        else:
            self.expr(node, tries)

    def _import(self, stmt):
        source_node = next((c for c in self.children(stmt) if c.type == "string"), None)
        source = self.string_value(source_node) if source_node is not None else None
        if not source:
            return
        line = self.line(stmt)
        clause = next((c for c in self.children(stmt) if c.type == "import_clause"), None)
        if clause is None:
            self._add_binding(None, None, source, "side-effect", line)
            return
        for c in self.children(clause):
            if c.type == "identifier":  # default import
                self._add_binding(self.text(c), "default", source, "default", line)
            elif c.type == "namespace_import":
                name = next((self.text(g) for g in self.children(c) if g.type == "identifier"), None)
                if name:
                    self._add_binding(name, "*", source, "namespace", line)
            elif c.type == "named_imports":
                for spec in self.children(c, "import_specifier"):
                    idents = [g for g in self.children(spec) if g.type == "identifier"]
                    imported = self.text(idents[0]) if idents else None
                    local = self.text(idents[1]) if len(idents) > 1 else imported
                    if imported:
                        self._add_binding(local or imported, imported, source, "named", line)

    def _add_binding(self, local, imported, source, kind, line):
        self.model["imports"].append({"local": local, "imported": imported, "source": source, "kind": kind, "line": line})
        if local:
            self.bindings[local] = {"source": source, "imported": imported, "kind": kind}

    def _export(self, stmt, tries):
        decl = self.field(stmt, "declaration")
        is_default = any(c.type == "default" for c in self.children(stmt))
        if decl is not None:
            if decl.type == "function_declaration":
                self._function_decl(decl, exported=True, default=is_default)
            elif decl.type == "class_declaration":
                self._class_decl(decl, exported=True, default=is_default)
            elif decl.type in ("lexical_declaration", "variable_declaration"):
                for d in self.children(decl, "variable_declarator"):
                    self._declarator(d, tries, top=not self.fn_stack, exported=True, default=is_default)
            return
        clause = next((c for c in self.children(stmt) if c.type == "export_clause"), None)
        if clause is not None:
            quals = {f["qual"] for f in self.model["functions"]}
            classes = {c["name"] for c in self.model["classes"]}
            for spec in self.children(clause, "export_specifier"):
                idents = [g for g in self.children(spec) if g.type == "identifier"]
                if not idents:
                    continue
                name = self.text(idents[-1])
                src_name = self.text(idents[0]) if len(idents) > 1 else name
                if src_name in quals:
                    self.model["exports"][name] = {"kind": "function", "qual": src_name}
                elif src_name in classes:
                    self.model["exports"][name] = {"kind": "class", "name": src_name}
                elif src_name in self.model["routers"]:
                    self.model["exports"][name] = {"kind": "router", "var": src_name}
                elif src_name in self.model["apps"]:
                    self.model["exports"][name] = {"kind": "app", "var": src_name}
                else:
                    self.model["exports"][name] = {"kind": "const"}
            return
        if is_default:  # `export default <expression>`
            expr = next((c for c in self.children(stmt) if c.type in (
                "identifier", "arrow_function", "function_expression", "call_expression", "new_expression",
                "object", "member_expression", "await_expression")), None)
            if expr is not None:
                self._default_export(expr, tries)

    def _default_export(self, expr, tries):
        t = expr.type
        if t in ("arrow_function", "function_expression"):
            self._function_record(expr, "default", exported=True, default=True)
        elif t == "identifier":
            self._export_named_binding(self.text(expr))
        elif t in ("call_expression", "new_expression"):
            self.expr(expr, tries)
            p = self.parts(self.field(expr, "function") or self.field(expr, "constructor")) or []
            fq = self._lib_fq(p)
            if fq in APP_CTOR_LIBS or fq in ROUTER_CTOR_LIBS:
                table = self.model["apps"] if fq in APP_CTOR_LIBS else self.model["routers"]
                if table:
                    var = max(table.items(), key=lambda kv: kv[1]["line"])[0]
                    self.model["exports"]["default"] = {"kind": "app" if fq in APP_CTOR_LIBS else "router", "var": var}
                return
            self.model["exports"]["default"] = {"kind": "const"}
        else:
            self.model["exports"]["default"] = {"kind": "const"}

    def _export_named_binding(self, name: str):
        """`export default x` / `module.exports = x` for a local binding x."""
        quals = {f["qual"]: f for f in self.model["functions"]}
        if name in quals:
            quals[name]["exported"] = True
            self.model["exports"]["default"] = {"kind": "function", "qual": name}
        elif name in self.model["apps"]:
            self.model["exports"]["default"] = {"kind": "app", "var": name}
        elif name in self.model["routers"]:
            self.model["exports"]["default"] = {"kind": "router", "var": name}
        elif name in self.model["instances"]:
            inst = dict(self.model["instances"][name])
            inst["export_var"] = name
            self.model["exports"]["default"] = inst
        elif name in {c["name"] for c in self.model["classes"]}:
            self.model["exports"]["default"] = {"kind": "class", "name": name}
        else:
            self.model["exports"]["default"] = {"kind": "const", "var": name}

    # -- declarations ---------------------------------------------------------
    def _function_decl(self, node, exported: bool, default: bool = False):
        name_node = self.field(node, "name")
        name = self.text(name_node) if name_node is not None else "default"
        self._function_record(node, name, exported=exported, default=default)

    def _function_record(self, node, qual: str, exported: bool, default: bool = False,
                         cls: str | None = None, name_override: str | None = None) -> dict:
        args: list[str] = []
        params = self.field(node, "parameters")
        if params is not None:
            for p in self.children(params):
                pid = self.field(p, "pattern") if p.type in ("required_parameter", "optional_parameter") else p
                if pid is not None and pid.type in ("identifier", "shorthand_property_identifier_pattern"):
                    args.append(self.text(pid))
        is_plugin = default and cls is None and len(args) == 1 and not self.model["is_test"]
        rec = {
            "qual": qual, "name": name_override or qual.split(".")[-1], "line": self.line(node),
            "is_async": any(c.type == "async" for c in self.children(node)),
            "cls": cls, "exported": exported, "args": args, "plugin": is_plugin,
            "calls": [], "env_reads": [], "http": [], "db": [], "local_new": [],
            "local_consts": {}, "broad_catches": [],
        }
        self.model["functions"].append(rec)
        self._walked_fns.add(id(node))
        if is_plugin:
            self.model["exports"]["default"] = {"kind": "plugin"}
        elif default and cls is None:
            self.model["exports"]["default"] = {"kind": "function", "qual": qual}
        elif exported and cls is None:
            self.model["exports"].setdefault(qual, {"kind": "function", "qual": qual})
        body = self.field(node, "body")
        self.fn_stack.append(rec)
        self.rec_stack.append(rec)
        prev_plugin = self.plugin_param
        if is_plugin:
            self.plugin_param = args[0]
        if body is not None:
            if body.type == "statement_block":
                for s in self.children(body):
                    self.stmt_or_expr(s, [])
            else:
                self.expr(body, [])
        self.plugin_param = prev_plugin
        self.rec_stack.pop()
        self.fn_stack.pop()
        return rec

    def _class_decl(self, node, exported: bool, default: bool = False):
        name_node = self.field(node, "name")
        name = self.text(name_node) if name_node is not None else "AnonymousClass"
        self.model["classes"].append({"name": name, "line": self.line(node)})
        if exported:
            self.model["exports"]["default" if default else name] = {"kind": "class", "name": name}
        body = self.field(node, "body")
        if body is not None:
            for m in self.children(body, "method_definition"):
                mname_node = self.field(m, "name")
                mname = self.text(mname_node) if mname_node is not None else "?"
                self._function_record(m, f"{name}.{mname}", exported=False, cls=name)

    def _declarator(self, d, tries, top: bool, exported: bool = False, default: bool = False):
        name_node = self.field(d, "name")
        value = self.field(d, "value")
        if name_node is None:
            return
        line = self.line(d)
        # destructuring: const { Pool } = require('pg') / const { X = 1 } = process.env
        if name_node.type == "object_pattern" and value is not None:
            if value.type == "call_expression" and self.parts(self.field(value, "function")) == ["require"]:
                src_node = next((a for a in _real_args(self.field(value, "arguments")) if a.type == "string"), None)
                source = self.string_value(src_node) if src_node is not None else None
                if source:
                    for pair in self.children(name_node):
                        if pair.type == "pair_pattern":
                            key, val = self.field(pair, "key"), self.field(pair, "value")
                            alias = self.text(val) if val is not None and val.type == "identifier" else None
                            if key is not None:
                                self._add_binding(alias or self.text(key), self.text(key), source, "named", line)
                        elif pair.type == "shorthand_property_identifier_pattern":
                            n = self.text(pair)
                            self._add_binding(n, n, source, "named", line)
                return
            if self.parts(value) == ["process", "env"]:
                for pair in self.children(name_node):
                    key = None
                    has_default = False
                    if pair.type == "shorthand_property_identifier_pattern":
                        key = self.text(pair)
                    elif pair.type == "pair_pattern":
                        k = self.field(pair, "key")
                        key = self.text(k) if k is not None else None
                        has_default = self.field(pair, "value") is not None
                    if key and self.rec_stack:
                        self.rec_stack[-1]["env_reads"].append(
                            {"var": key, "line": line, "has_default": has_default, "tries": tries})
                return
        if name_node.type != "identifier":
            if value is not None:
                self.expr(value, tries)  # e.g. const { rows } = await pool.query(...)
            return
        name = self.text(name_node)

        if value.type in ("arrow_function", "function_expression"):
            self._function_record(value, name, exported=exported, default=default)
            return
        if value.type in ("call_expression", "new_expression"):
            self.expr(value, tries)
            fn_node = self.field(value, "function") or self.field(value, "constructor")
            p = self.parts(fn_node) or []
            fq = self._lib_fq(p)
            if top:
                exp_key = "default" if default else name
                if fq in APP_CTOR_LIBS:
                    self.model["apps"][name] = {"framework": APP_CTOR_LIBS[fq], "line": self.line(value)}
                    if exported:
                        self.model["exports"][exp_key] = {"kind": "app", "var": name}
                    return
                if fq in ROUTER_CTOR_LIBS:
                    opts = self._options(_real_args(self.field(value, "arguments")))
                    self.model["routers"][name] = {"framework": ROUTER_CTOR_LIBS[fq],
                                                   "prefix": opts.get("prefix") if isinstance(opts.get("prefix"), str) else "",
                                                   "line": self.line(value)}
                    if exported:
                        self.model["exports"][exp_key] = {"kind": "router", "var": name}
                    return
                if fq in DB_LIB_CALLS:
                    self.model["instances"][name] = {"lib": fq, "engine": DB_LIB_CALLS[fq], "kind": "db"}
                    if exported:
                        self.model["exports"][exp_key] = dict(self.model["instances"][name])
                    return
                if fq in DB_CLASS_CTORS:
                    self.model["instances"][name] = {"lib": fq, "engine": DB_CLASS_CTORS[fq], "kind": "db"}
                    if exported:
                        self.model["exports"][exp_key] = dict(self.model["instances"][name])
                    return
                if fq == "axios.create":
                    opts = self._options(_real_args(self.field(value, "arguments")))
                    self.model["instances"][name] = {"lib": "axios", "engine": None, "kind": "http-instance",
                                                     "timeout": bool(opts.get("timeout")),
                                                     "base": opts.get("baseURL") if isinstance(opts.get("baseURL"), str) else None}
                    if exported:
                        self.model["exports"][exp_key] = dict(self.model["instances"][name])
                    return
                if p == ["require"]:
                    src_node = next((a for a in _real_args(self.field(value, "arguments")) if a.type == "string"), None)
                    source = self.string_value(src_node) if src_node is not None else None
                    if source:
                        self._add_binding(name, "default", source, "require-default", line)
                    return
                if value.type == "new_expression":
                    self.model["instances"][name] = {"kind": "new", "parts": p}
                return
            if self.fn_stack and value.type == "new_expression":
                self.fn_stack[-1]["local_new"].append({"var": name, "parts": p, "line": self.line(value)})
            return
        # other values (await/binary/member/...): walk for nested calls and env reads,
        # and keep string/env consts as URL hints
        self.expr(value, tries)
        hint = self._const_hint(value)
        if hint:
            if top:
                self.model["module_consts"][name] = hint
            elif self.fn_stack:
                self.fn_stack[-1]["local_consts"][name] = hint

    def _const_hint(self, value) -> dict | None:
        if value.type == "string":
            s = self.string_value(value)
            return {"str": s} if s is not None else None
        var = self._env_member(value)
        if var:
            return {"env": var}
        if value.type == "template_string":
            envs = [self._env_member(sub) for sub in self._substitutions(value)]
            if envs and all(envs):
                return {"env": envs[0]}
        return None

    def _substitutions(self, template_node) -> list:
        out = []
        for c in self.children(template_node):
            if c.type != "template_substitution":
                continue
            inner = [g for g in self.children(c) if g.type not in ("${", "}")]
            out.extend(inner)
        return out

    def _env_member(self, node) -> str | None:
        """process.env.X / process.env['X'] -> X."""
        if node is None:
            return None
        if node.type == "member_expression":
            if self.parts(self.field(node, "object")) == ["process", "env"]:
                prop = self.field(node, "property")
                if prop is not None and prop.type == "property_identifier":
                    return self.text(prop)
        if node.type == "subscript_expression":
            if self.parts(self.field(node, "object")) == ["process", "env"]:
                return self.string_value(self.field(node, "index"))
        return None

    def _options(self, real_args: list) -> dict:
        """Object-literal argument -> {key: static scalar value}."""
        obj = next((a for a in real_args if a.type == "object"), None)
        if obj is None:
            return {}
        out = {}
        for pair in self.children(obj, "pair"):
            key, val = self.field(pair, "key"), self.field(pair, "value")
            if key is None or val is None:
                continue
            if val.type == "string":
                out[self.text(key)] = self.string_value(val)
            elif val.type == "number":
                try:
                    out[self.text(key)] = float(self.text(val))
                except ValueError:
                    pass
            elif val.type in ("true", "false"):
                out[self.text(key)] = val.type == "true"
        return out

    def _lib_fq(self, parts: list[str]) -> str | None:
        """Callee head resolved through import bindings -> 'pkg.name' for bare-package imports."""
        if not parts:
            return None
        b = self.bindings.get(parts[0])
        if b and not b["source"].startswith("."):
            if b["imported"] in ("default", "*"):
                return ".".join([b["source"]] + parts[1:])
            return ".".join([b["source"], b["imported"]] + parts[1:])
        return None

    # -- control flow -----------------------------------------------------------
    def _try(self, stmt, tries):
        handlers = []
        for c in self.children(stmt, "catch_clause"):
            body = self.field(c, "body")
            reraises = body is not None and self._has_throw(body)
            handlers.append({"names": ["*"], "reraises": reraises, "line": self.line(c)})
            if self.field(c, "parameter") is None and body is not None and self._is_swallow(body) and self.rec_stack:
                self.rec_stack[-1].setdefault("broad_catches", []).append(self.line(c))
        inner = tries + [handlers] if handlers else tries
        for c in self.children(stmt):
            if c.type == "statement_block":
                for s in self.children(c):
                    self.stmt_or_expr(s, inner)
            elif c.type == "catch_clause":
                body = self.field(c, "body")
                if body is not None:
                    for s in self.children(body):
                        self.stmt_or_expr(s, tries)
            elif c.type == "finally_clause":
                for s in self.children(c):
                    self.stmt_or_expr(s, tries)

    def _has_throw(self, node) -> bool:
        stack = [node]
        while stack:
            n = stack.pop()
            if n.type == "throw_statement":
                return True
            stack.extend(n.children)
        return False

    def _is_swallow(self, body) -> bool:
        stmts = [c for c in self.children(body) if c.type.endswith("_statement")]
        if not stmts:
            return True
        for s in stmts:
            if s.type != "expression_statement":
                return False
            exprs = self.children(s)
            p = self.parts(self.field(exprs[0], "function")) if exprs and exprs[0].type == "call_expression" else None
            if not (len(p or []) == 2 and p[0] == "console"):
                return False
        return True

    # -- expressions ------------------------------------------------------------
    def expr(self, node, tries):
        if node is None:
            return
        t = node.type
        if t in ("call_expression", "new_expression"):
            self._call(node, tries)
        elif t == "member_expression":
            if self._record_env(self._env_member(node), node, tries, has_default=False):
                return
            self.expr(self.field(node, "object"), tries)
        elif t == "subscript_expression":
            if self._record_env(self._env_member(node), node, tries, has_default=False):
                return
            self.expr(self.field(node, "object"), tries)
            self.expr(self.field(node, "index"), tries)
        elif t == "assignment_expression":
            self._assignment(node, tries)
        elif t == "binary_expression":
            op = self.text(self.field(node, "operator"))
            var = self._env_member(self.field(node, "left")) if op in ("||", "??") else None
            if var:  # process.env.X || 'default'
                self._record_env(var, node, tries, has_default=True)
            self.expr(self.field(node, "left"), tries)
            self.expr(self.field(node, "right"), tries)
        elif t == "template_string":
            for sub in self._substitutions(node):
                self.expr(sub, tries)
        elif t in ("arrow_function", "function_expression", "function_declaration"):
            if id(node) in self._walked_fns:
                return  # already walked as a named/inline handler
            # anonymous callback (.then(r => ...), middleware): its body belongs to
            # the enclosing named function - walk it, but create no component
            self._walked_fns.add(id(node))
            body = self.field(node, "body")
            if body is not None:
                if body.type == "statement_block":
                    for s in self.children(body):
                        self.stmt_or_expr(s, tries)
                else:
                    self.expr(body, tries)
        elif t in ("await_expression", "unary_expression", "spread_element", "parenthesized_expression",
                   "augmented_assignment_expression", "ternary_expression", "update_expression"):
            for c in self.children(node):
                self.expr(c, tries)
        elif t == "object":
            for pair in self.children(node):
                for side in ("key", "value"):
                    self.expr(self.field(pair, side), tries)
        elif t == "array":
            for c in self.children(node):
                self.expr(c, tries)
        elif t == "pair":
            self.expr(self.field(node, "value"), tries)
        # identifiers, literals, types: nothing to do

    def _record_env(self, var, node, tries, has_default: bool) -> bool:
        if not var:
            return False
        read = {"var": var, "line": self.line(node), "has_default": has_default, "tries": tries}
        if self.rec_stack and not self.model["is_test"]:
            self.rec_stack[-1]["env_reads"].append(read)
        else:  # module-level read (e.g. new Pool({connectionString: process.env.DB_URL}))
            self.model.setdefault("module_env_reads", []).append(read)
        return True

    def _assignment(self, node, tries):
        left, right = self.field(node, "left"), self.field(node, "right")
        lp = self.parts(left) if left is not None else None
        if self.model["is_test"] or self.fn_stack:
            pass
        # module.exports = ... / exports.foo = ...
        if lp and lp[0] in ("module", "exports") and not self.fn_stack:
            if lp == ["module", "exports"]:
                if right is not None and right.type == "identifier":
                    self._export_named_binding(self.text(right))
                elif right is not None and right.type == "object":
                    self._object_export(right, tries)
                self.expr(right, tries)
                return
            if len(lp) == 2 and lp[0] == "exports":
                if right is not None and right.type in ("arrow_function", "function_expression"):
                    self._function_record(right, lp[1], exported=True)
                    return
                self.model["exports"][lp[1]] = {"kind": "const"}
                self.expr(right, tries)
                return
        if left is not None and left.type == "member_expression":
            var = self._env_member(left)
            if var:
                self._record_env(var, node, tries, has_default=False)
                return
            # this.apiBase = process.env.X -> class attribute env hint for URL resolution
            lp = self.parts(left) or []
            if len(lp) == 2 and lp[0] == "this" and self.fn_stack and self.fn_stack[-1].get("cls"):
                env_var = self._env_member(right)
                if env_var:
                    cls = self.fn_stack[-1]["cls"]
                    self.model["class_attr_env"].setdefault(cls, {})[lp[1]] = env_var
                    self._record_env(env_var, right, tries, has_default=False)
                    return
        self.expr(left, tries)
        self.expr(right, tries)

    def _object_export(self, obj, tries):
        """module.exports = { query: fn, ... } -> object default export with method functions."""
        self.model["exports"]["default"] = {"kind": "object"}
        for pair in self.children(obj, "pair"):
            key, val = self.field(pair, "key"), self.field(pair, "value")
            if key is None or val is None:
                continue
            if val.type in ("arrow_function", "function_expression"):
                rec = self._function_record(val, self.text(key), exported=True)
                    # methods on the exported object are also reachable by name
                self.model["exports"][self.text(key)] = {"kind": "function", "qual": rec["qual"]}
            else:
                self.expr(val, tries)

    # -- calls -------------------------------------------------------------------
    def _call(self, call, tries):
        fn_node = self.field(call, "function") or self.field(call, "constructor")
        args = _real_args(self.field(call, "arguments"))
        p = self.parts(fn_node)
        line = self.line(call)
        handled = False

        # supertest: request(app).get('/path') — a chained call, so p is None here
        if fn_node is not None and fn_node.type == "member_expression":
            obj = self.field(fn_node, "object")
            if obj is not None and obj.type == "call_expression" and \
                    self.parts(self.field(obj, "function")) == ["request"] and args:
                tpl = self.path_template(args[0])
                if tpl is not None and self.model["is_test"]:
                    self._test_http((self.text(self.field(fn_node, "property")) or "GET").upper(), tpl, line)
                handled = True  # the inner request(app) is wiring, not a call

        if p and not handled:
            head, method = p[0], p[-1]
            # route registration: <app|router>.get('/path', handler[, middleware...])
            if method in HANDLER_METHODS and len(args) >= 2 and \
                    (head in self.model["routers"] or head in self.model["apps"]):
                tpl = self.path_template(args[0])
                if tpl is not None:
                    self.model["routes"].append({"router_var": head, "method": method.upper(), "path": tpl,
                                                 "handler": self._handler_ref(args[1:]), "line": line})
                    handled = True
            # fastify plugin: <param>.get('/x', h) inside an exported plugin function
            if not handled and method in HANDLER_METHODS and head == self.plugin_param and len(args) >= 2:
                tpl = self.path_template(args[0])
                if tpl is not None:
                    self.model["routes"].append({"router_var": "__plugin__", "method": method.upper(),
                                                 "path": tpl, "handler": self._handler_ref(args[1:]), "line": line})
                    handled = True
            # mounts: app.use('/pre', router) / app.use(router) / fastify app.register(plugin, {prefix})
            if not handled and method in ("use", "register") and head in self.model["apps"]:
                self._mount(head, args, line)
                handled = True
            if not handled and method == "use" and head in self.model["routers"]:
                self._mount(head, args, line)
                handled = True
            # jest/mocha: test('name', fn) / it.skip('name', fn) / describe('...', fn)
            if not handled and self.model["is_test"] and head in ("test", "it", "describe") and len(p) <= 2:
                name = self._test_name(args[0])
                fn_arg = next((a for a in args[1:] if a.type in ("arrow_function", "function_expression")), None)
                if name is not None and head != "describe":
                    self._test_case(name, line, fn_arg)
                    handled = True  # describe bodies are walked via the args loop below
            # http clients
            if not handled:
                http = self._http_call(call, p, args, tries)
                if http is not None:
                    handled = True
                    url = http.get("url_static")
                    if self.model["is_test"]:
                        if url and (url.startswith("/") or url.startswith(("http://localhost", "http://127.0.0.1"))):
                            path = "/" + url.split("/", 3)[-1] if url.startswith("http") and url.count("/") >= 3 else url
                            self._test_http("GET", path, line)
                    elif self.rec_stack:
                        self.rec_stack[-1]["http"].append(http)
                    # module-import-time HTTP in non-test files: not modelled (python parity)
            # databases: mongoose.connect(...) / new Pool(...)
            if not handled:
                db = self._db_call(call, p, tries)
                if db is not None:
                    handled = True
                    if self.rec_stack and not self.model["is_test"]:
                        self.rec_stack[-1]["db"].append(db)
            # module-level db/http/class instances used at module scope
            if not handled and len(p) == 2 and head in self.model["instances"] and not self.fn_stack:
                inst = self.model["instances"][head]
                if inst.get("kind") == "db" and method in DB_INSTANCE_METHODS:
                    handled = True
                    if self.rec_stack and not self.model["is_test"]:
                        self.rec_stack[-1]["db"].append({"engine": inst["engine"], "lib": inst["lib"],
                                                         "line": line, "tries": tries, "kind": "instance-use"})
            if not handled and self.rec_stack:
                self.rec_stack[-1]["calls"].append({"parts": p, "line": line, "tries": tries})
            # walk the callee object chain so chained calls (a.b(...).c(...)) are seen
            if fn_node is not None and fn_node.type == "member_expression" and not handled:
                self.expr(self.field(fn_node, "object"), tries)
        for a in args:
            self.expr(a, tries)

    def _test_name(self, arg) -> str | None:
        if arg is None:
            return None
        s = self.string_value(arg)
        if s is not None:
            return s
        if arg.type == "template_string":
            return self.path_template(arg)
        return None

    def _test_case(self, name: str, line: int, fn_arg):
        rec = {"name": name, "line": line, "calls": [], "http": []}
        self.model["tests"].append(rec)
        if fn_arg is None:
            return
        self.rec_stack.append(rec)
        self._walked_fns.add(id(fn_arg))
        body = self.field(fn_arg, "body")
        if body is not None:
            if body.type == "statement_block":
                for s in self.children(body):
                    self.stmt_or_expr(s, [])
            else:
                self.expr(body, [])
        self.rec_stack.pop()

    def _test_http(self, method: str, path: str, line: int):
        if self.rec_stack and isinstance(self.rec_stack[-1].get("http"), list):
            self.rec_stack[-1]["http"].append({"method": method, "path": path, "line": line})
        else:  # module-level helper in a test file: keep it on a synthetic record
            if "_module_http" not in self.model:
                self.model["_module_http"] = []
            self.model["_module_http"].append({"method": method, "path": path, "line": line})

    def _handler_ref(self, args) -> dict | None:
        """Last non-path argument as handler: inline function or identifier reference."""
        if not args:
            return None
        last = args[-1]
        if last.type in ("arrow_function", "function_expression"):
            line = self.line(last)
            self._function_record(last, f"inline@{line}", exported=False,
                                  name_override=f"inline handler (line {line})")
            return {"inline": line}
        if last.type == "identifier":
            return {"local": self.text(last)}
        if last.type == "member_expression":  # e.g. router.post('/', orders.createOrder)
            p = self.parts(last)
            if p and len(p) == 2:
                return {"obj": p[0], "method": p[1]}
        return None

    def _mount(self, app_var: str, args, line: int):
        prefix, ref = "", None
        for a in args:
            if a.type == "string" and ref is None:
                s = self.string_value(a)
                if s is not None:
                    prefix = s
            elif a.type == "object":
                opts = self._options([a])
                if isinstance(opts.get("prefix"), str):
                    prefix = opts["prefix"]
            elif a.type == "identifier" and ref is None:
                ref = {"local": self.text(a)}
        if ref is not None:
            self.model["mounts"].append({"app_var": app_var, "router_ref": ref, "prefix": prefix, "line": line})

    def _http_call(self, call, p: list[str], args, tries) -> dict | None:
        head, method = p[0], p[-1]
        lib, url_node, opts_node = None, None, None
        b = self.bindings.get(head)
        if len(p) == 1:
            if head == "fetch" or (b and b["source"] in FETCH_PACKAGES):
                lib = "fetch"
                url_node, opts_node = (args + [None, None])[:2]
            elif b and b["source"] in AXIOS_PACKAGES:
                lib = "axios"  # axios(config)
                if args and args[0].type == "object":
                    opts_node = args[0]
                else:
                    url_node = args[0] if args else None
            elif head == "got" or (b and b["source"] in GOT_PACKAGES):
                lib = "got"
                url_node = args[0] if args else None
        else:
            if b and b["source"] in AXIOS_PACKAGES and method in HTTP_VERBS | {"request"}:
                lib = f"axios.{method}"
                url_node = args[0] if args else None
                opts_node = args[1] if len(args) > 1 else None
            elif head in self.model["instances"] and self.model["instances"][head].get("kind") == "http-instance":
                if method in HTTP_VERBS | {"request"}:
                    inst = self.model["instances"][head]
                    lib = f"{inst['lib']}.{method}"
                    url_node = args[0] if args else None
                    opts_node = args[1] if len(args) > 1 else None
            elif b and b["source"] in NODE_HTTP_PACKAGES and method in ("get", "request", "post"):
                lib = f"{b['source']}.{method}"
                url_node = args[0] if args else None
                opts_node = args[1] if len(args) > 1 else None
            elif b and b["source"] in GOT_PACKAGES and method in HTTP_VERBS | {"request", "stream"}:
                lib = f"got.{method}"
                url_node = args[0] if args else None
        if lib is None:
            return None
        timeout = False
        if opts_node is not None and opts_node.type == "object":
            for pair in self.children(opts_node, "pair"):
                key = self.field(pair, "key")
                if key is not None and self.text(key) in ("timeout", "signal"):
                    timeout = True
        if len(p) == 2 and head in self.model["instances"] and \
                self.model["instances"][head].get("kind") == "http-instance" and \
                self.model["instances"][head].get("timeout"):
            timeout = True
        hint = self._url_hint(url_node)
        static = hint.get("static")
        if static and not static.startswith(("http://", "https://")):
            inst_base = self.model["instances"].get(head, {}).get("base")
            if inst_base:
                hint["static"] = inst_base.rstrip("/") + "/" + static.lstrip("/")
        return {"lib": lib, "line": self.line(call), "guarded": bool(tries) or self._promise_catch_guard(call),
                "timeout": timeout or lib.startswith("got."), "tries": tries,
                "url_static": hint.get("static"), "url_env": hint.get("env")}

    def _promise_catch_guard(self, call) -> bool:
        parent = call.parent
        while parent is not None and parent.type in ("await_expression", "parenthesized_expression"):
            parent = parent.parent
        if parent is not None and parent.type == "member_expression":
            prop = self.field(parent, "property")
            if prop is not None and self.text(prop) == "catch":
                return True
        return False

    def _url_hint(self, url_node) -> dict:
        if url_node is None:
            return {}
        if url_node.type == "template_string":
            # prefer an env hint from the substitutions: `${base}/v1` with base = process.env.BASE
            for sub in self._substitutions(url_node):
                var = self._env_member(sub)
                if var:
                    return {"env": var}
                var = self._const_env_ref(sub)
                if var:
                    return {"env": var}
        var = self._env_member(url_node)
        if var:
            return {"env": var}
        tpl = self.path_template(url_node)
        if tpl:
            return {"static": tpl}
        if url_node.type == "identifier":
            name = self.text(url_node)
            for frame in reversed(self.fn_stack):
                if name in frame["local_consts"]:
                    c = frame["local_consts"][name]
                    return {"env": c["env"]} if c.get("env") else {"static": c.get("str")}
            c = self.model["module_consts"].get(name)
            if c:
                return {"env": c["env"]} if c.get("env") else {"static": c.get("str")}
        return {}

    def _const_env_ref(self, node) -> str | None:
        """Identifier / this.attr whose recorded value is an env var -> VAR."""
        if node is None:
            return None
        if node.type == "identifier":
            name = self.text(node)
            for frame in reversed(self.fn_stack):
                if name in frame["local_consts"] and frame["local_consts"][name].get("env"):
                    return frame["local_consts"][name]["env"]
            c = self.model["module_consts"].get(name)
            if c and c.get("env"):
                return c["env"]
        if node.type == "member_expression" and self.parts(self.field(node, "object")) == ["this"]:
            attr = self.text(self.field(node, "property"))
            fn = self.fn_stack[-1] if self.fn_stack else None
            if fn and fn.get("cls"):
                var = self.model.get("class_attr_env", {}).get(fn["cls"], {}).get(attr)
                if var:
                    return var
        return None

    def _db_call(self, call, p, tries) -> dict | None:
        if len(p) != 1:
            fq = self._lib_fq(p)
            if fq in DB_LIB_CALLS:
                return {"engine": DB_LIB_CALLS[fq], "lib": fq, "line": self.line(call), "tries": tries, "kind": "connect"}
        if call.type == "new_expression":
            fq = self._lib_fq(p)
            if fq in DB_CLASS_CTORS:
                return {"engine": DB_CLASS_CTORS[fq], "lib": fq, "line": self.line(call), "tries": tries, "kind": "connect"}
        else:
            fq = self._lib_fq(p)
            if len(p) == 1 and fq in DB_LIB_CALLS:
                return {"engine": DB_LIB_CALLS[fq], "lib": fq, "line": self.line(call), "tries": tries, "kind": "connect"}
        return None


def extract_file(rel: str, source: str, lang: str) -> dict:
    return _Extractor(rel, source, lang).extract()


# --------------------------------------------------------------------------- resolution + graph assembly
class JSAnalyzer:
    """Joins per-file models into the shared component/edge format."""

    def __init__(self, root: Path, tree: dict | None = None, cache: FileCache | None = None):
        self.root = Path(root)
        self.tree = tree
        self.cache = cache or FileCache(None, use_cache=False)
        self.models: dict[str, dict] = {}     # rel -> model
        self.by_module: dict[str, dict] = {}  # module name -> model
        self.bindings: dict[str, dict[str, dict]] = {}  # rel -> local -> resolved binding
        self.class_methods: dict[str, dict[str, str]] = {}  # class component id -> method name -> func id
        self.components: dict[str, dict] = {}
        self.edges: dict[str, dict] = {}
        self.frameworks: set[str] = set()
        self.third_party: set[str] = set()
        self.stats = {"resolved_calls": 0, "unresolved_calls": 0, "library_calls": 0, "functions": 0, "classes": 0}
        self.parse_errors: list[dict] = []
        self.secret_hits: list[dict] = []
        self.http_findings: list[dict] = []
        self.broad_excepts: list[dict] = []
        self.env_required: list[dict] = []

    # -- shared component/edge helpers (identical semantics to python_analyzer)
    def snippet(self, model: dict, line: int | None) -> str:
        lines = model.get("_lines") or []
        if not line or line > len(lines):
            return ""
        return lines[line - 1]

    def ev(self, model: dict, line: int | None, detail: str = "", **extra) -> dict:
        return {"file": model["rel"], "line": line, "snippet": self.snippet(model, line), "detail": detail, **extra}

    def add_component(self, cid, ctype, name, file=None, line=None, **data):
        if cid not in self.components:
            self.components[cid] = {"id": cid, "type": ctype, "name": name, "file": file, "line": line, "data": data}
        else:
            self.components[cid]["data"].update({k: v for k, v in data.items() if v is not None})
        return self.components[cid]

    def add_edge(self, source, target, etype, classification, evidence, guard=None):
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

    # -- pipeline
    def run(self, progress=None) -> dict:
        def step(name, frac):
            if progress:
                progress(name, frac)

        step("Scanning files", 0.05)
        if self.tree is None:
            self.tree = scan_tree(self.root)
        files = [f for f in self.tree["files"]
                 if f.split("/")[-1].endswith(tuple(JS_LANGS))
                 and not f.split("/")[-1].endswith(".d.ts")
                 and ".min." not in f.split("/")[-1]]

        step("Parsing JavaScript/TypeScript sources", 0.2)
        file_set = set(self.tree["files"])
        for rel in files:
            source = (self.root / rel).read_text(encoding="utf-8", errors="replace")
            model = self.cache.load(rel, source)
            if model is None:
                lang = JS_LANGS["." + rel.rsplit(".", 1)[-1]]
                model = extract_file(rel, source, lang)
                self.cache.misses += 1
                self.cache.store(rel, source, {k: v for k, v in model.items() if not k.startswith("_")})
            model["_lines"] = [redact(l.strip())[0][:220] for l in source.splitlines()]
            if model.get("parse_error"):
                self.parse_errors.append({"file": rel, "line": model["parse_error"]["line"],
                                          "error": model["parse_error"]["error"]})
            self.secret_hits.extend({"file": rel, "line": h["line"], "snippet": h["snippet"]}
                                    for h in model.get("secret_hits", []))
            self.models[rel] = model
            self.by_module.setdefault(model["module"], model)

        step("Indexing modules and imports", 0.4)
        self._resolve_bindings(file_set)
        self._index_components()

        step("Resolving routes and application wiring", 0.55)
        self._resolve_wiring()

        step("Resolving calls and resource usage", 0.7)
        for model in self.models.values():
            if not model["is_test"]:
                for fn in model["functions"]:
                    self._analyse_function(model, fn)

        step("Mapping tests to components", 0.8)
        self._analyse_tests()

        step("Reading dependency manifests", 0.9)
        manifests = self._read_manifests(self.tree["manifests"])
        routes = [c for c in self.components.values() if c["type"] == "route"]
        tested_routes = {e["target"] for e in self.edges.values() if e["type"] == "exercises"}
        total_calls = self.stats["resolved_calls"] + self.stats["unresolved_calls"]
        analysed = []
        if any(m["lang"] == "javascript" for m in self.models.values()):
            analysed.append("JavaScript")
        if any(m["lang"] in ("typescript", "tsx") for m in self.models.values()):
            analysed.append("TypeScript")
        coverage = {
            "js_files": len(files),
            "js_analysed_files": len(self.models),
            "parse_errors": self.parse_errors,
            "js_functions": self.stats["functions"],
            "js_classes": self.stats["classes"],
            "js_resolved_calls": self.stats["resolved_calls"],
            "js_unresolved_calls": self.stats["unresolved_calls"],
            "js_library_calls": self.stats["library_calls"],
            "js_call_resolution_rate": round(self.stats["resolved_calls"] / total_calls, 3) if total_calls else None,
            "routes": len(routes),
            "routes_with_tests": len([r for r in routes if r["id"] in tested_routes]),
            "tests": len([c for c in self.components.values() if c["type"] == "test"]),
        }
        return {
            "components": list(self.components.values()),
            "edges": [{k: v for k, v in e.items()} for e in self.edges.values()],
            "scan": {
                "file_count": self.tree["file_count"],
                "languages": dict(self.tree["languages"]),
                "analysed_languages": analysed,
                "manifests": self.tree["manifests"],
                "skipped_sensitive": len(self.tree["skipped_sensitive"]),
                "skipped_large": self.tree["skipped_large"],
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

    # -- bindings
    def _resolve_bindings(self, file_set: set[str]):
        for rel, model in self.models.items():
            table: dict[str, dict] = {}
            for imp in model["imports"]:
                source = imp["source"]
                if imp["local"] is None:
                    continue
                if source.startswith("."):
                    target = resolve_import(rel, source, file_set)
                    table[imp["local"]] = {
                        "module": self.models[target]["module"] if target else None,
                        "imported": imp["imported"], "kind": imp["kind"],
                    }
                else:
                    pkg = source if source.startswith("@") else source.split("/")[0]
                    if pkg in FRAMEWORK_IMPORTS:
                        self.frameworks.add(FRAMEWORK_IMPORTS[pkg])
                    if not model["is_test"]:
                        self.third_party.add(pkg)
                    table[imp["local"]] = {"package": pkg, "imported": imp["imported"], "kind": imp["kind"]}
            self.bindings[rel] = table

    # -- components
    def _index_components(self):
        for model in self.models.values():
            if not model["is_test"]:
                self.add_component(f"module:{model['module']}", "module", model["module"], model["rel"], 1)
        for model in self.models.values():
            if model["is_test"]:
                continue
            mid = f"module:{model['module']}"
            for cls in model["classes"]:
                cid = f"class:{model['module']}.{cls['name']}"
                self.add_component(cid, "class", cls["name"], model["rel"], cls["line"], module=model["module"])
                self.add_edge(mid, cid, "contains", "confirmed", self.ev(model, cls["line"]))
                self.stats["classes"] += 1
                self.class_methods[cid] = {}
            for fn in model["functions"]:
                fid = f"func:{model['module']}.{fn['qual']}"
                parent = f"class:{model['module']}.{fn['cls']}" if fn["cls"] else mid
                self.add_component(fid, "function", fn["name"], model["rel"], fn["line"], module=model["module"],
                                   is_async=fn["is_async"], cls=fn["cls"], args=fn["args"],
                                   exported=fn["exported"], inline=fn["qual"].startswith("inline@"))
                self.add_edge(parent, fid, "contains", "confirmed", self.ev(model, fn["line"]))
                self.stats["functions"] += 1
                if fn["cls"]:
                    self.class_methods[f"class:{model['module']}.{fn['cls']}"][fn["name"]] = fid
        for rel, model in self.models.items():
            if model["is_test"]:
                continue
            seen: set[str] = set()
            for imp in model["imports"]:
                if not imp["local"]:
                    continue
                b = self.bindings[rel].get(imp["local"], {})
                tm = b.get("module")
                if tm and tm != model["module"] and tm in self.by_module and tm not in seen:
                    seen.add(tm)
                    self.add_edge(f"module:{model['module']}", f"module:{tm}", "imports", "confirmed",
                                  self.ev(model, imp["line"]))
            # module-level env reads (e.g. new Pool({connectionString: process.env.DB_URL}))
            for read in model.get("module_env_reads", []):
                var = read["var"]
                env_id = f"env:{var}"
                self.add_component(env_id, "env_var", var, None, None)
                guard = list(read.get("tries") or [])
                if read["has_default"]:
                    guard = guard + [[{"names": ["<default>"], "reraises": False, "line": read["line"]}]]
                self.add_edge(f"module:{model['module']}", env_id, "reads_env", "confirmed",
                              self.ev(model, read["line"],
                                      "has default value" if read["has_default"] else "no default: value is undefined at runtime",
                                      has_default=read["has_default"]), guard)
                if not read["has_default"]:
                    self.env_required.append({"var": var, "component": f"module:{model['module']}",
                                              **self.ev(model, read["line"])})

    # -- apps / routers / routes
    def _router_fq(self, model: dict, var: str) -> str | None:
        if var == "__plugin__":
            return f"{model['module']}.default"
        if var in model["routers"] or var in model["apps"]:
            return f"{model['module']}.{var}"
        b = self.bindings[model["rel"]].get(var)
        if b and b.get("module") in self.by_module:
            tm = self.by_module[b["module"]]
            for key in filter(None, [b["imported"] if b["imported"] not in ("*",) else None, "default"]):
                exp = tm["exports"].get(key, {})
                if exp.get("kind") in ("router", "app", "plugin") and (exp.get("var") or exp.get("kind") == "plugin"):
                    if exp.get("kind") == "plugin":
                        return f"{tm['module']}.default"
                    return f"{tm['module']}.{exp['var']}"
        return None

    def _resolve_wiring(self):
        self.apps: dict[str, dict] = {}
        self.routers: dict[str, dict] = {}
        self.include_map: dict[str, dict] = {}
        for model in self.models.values():
            if model["is_test"]:
                continue
            for var, info in model["apps"].items():
                fq = f"{model['module']}.{var}"
                app_id = f"app:{fq}"
                self.apps[fq] = {"id": app_id, "framework": info["framework"], "model": model}
                self.add_component(app_id, "app", f"{var} ({info['framework']})", model["rel"], info["line"],
                                   framework=info["framework"], module=model["module"])
                self.add_edge(f"module:{model['module']}", app_id, "contains", "confirmed",
                              self.ev(model, info["line"]))
            for var, info in model["routers"].items():
                self.routers[f"{model['module']}.{var}"] = {"framework": info["framework"],
                                                            "prefix": info.get("prefix", ""),
                                                            "model": model, "plugin": False}
        for model in self.models.values():  # fastify plugin modules act as routers
            if model["exports"].get("default", {}).get("kind") == "plugin":
                self.routers[f"{model['module']}.default"] = {"framework": "fastify", "prefix": "",
                                                              "model": model, "plugin": True}
        for model in self.models.values():
            if model["is_test"]:
                continue
            for m in model["mounts"]:
                owner = self._router_fq(model, m["app_var"])
                router = self._router_fq(model, m["router_ref"]["local"]) if m["router_ref"].get("local") else None
                if owner and router and router != owner:
                    self.include_map[router] = {"owner": owner, "prefix": m["prefix"],
                                                "ev": self.ev(model, m["line"], "router mounted")}
        for model in self.models.values():
            if model["is_test"]:
                continue
            for r in model["routes"]:
                self._route(model, r)

    def _mount(self, router_fq: str) -> tuple[str, str | None, dict | None]:
        prefix, ev, seen, cur = "", None, set(), router_fq
        while cur not in self.apps and cur in self.include_map and cur not in seen:
            seen.add(cur)
            inc = self.include_map[cur]
            prefix = inc["prefix"] + prefix
            ev = ev or inc["ev"]
            cur = inc["owner"]
        return prefix, (cur if cur in self.apps else None), ev

    def _route(self, model: dict, r: dict):
        router_fq = self._router_fq(model, r["router_var"])
        if router_fq is None:
            return
        if router_fq in self.apps:
            include_prefix, app_fq, inc_ev = "", router_fq, None
            framework, router_prefix = self.apps[router_fq]["framework"], ""
        else:
            info = self.routers.get(router_fq)
            if info is None:
                return
            include_prefix, app_fq, inc_ev = self._mount(router_fq)
            framework = info["framework"]
            router_prefix = info.get("prefix", "")
        path = norm_path(r["path"])
        full = norm_path((include_prefix or "") + (router_prefix or "") + path)
        method = r["method"]
        rid = f"route:{method} {full}"
        handler_id = self._handler_id(model, r.get("handler"))
        self.add_component(rid, "route", f"{method} {full}", model["rel"], r["line"], method=method, path=full,
                           framework=framework, handler=handler_id)
        if handler_id:
            self.add_edge(rid, handler_id, "handled_by", "confirmed",
                          self.ev(model, r["line"], f"{r['router_var']}.{method.lower()}('{r['path']}', handler)"))
        if app_fq:
            cls = "confirmed" if (router_fq in self.apps or inc_ev) else "inferred"
            self.add_edge(rid, self.apps[app_fq]["id"], "served_by", cls,
                          inc_ev or self.ev(model, r["line"], "route registered on application"))
        elif len(self.apps) == 1:
            only = next(iter(self.apps.values()))["id"]
            self.add_edge(rid, only, "served_by", "inferred",
                          self.ev(model, r["line"], "single application in project; router mount not statically resolved"))

    def _handler_id(self, model: dict, ref) -> str | None:
        if not ref:
            return None
        if "inline" in ref:
            return f"func:{model['module']}.inline@{ref['inline']}"
        if "obj" in ref:
            b = self.bindings[model["rel"]].get(ref["obj"])
            if b and b.get("module") in self.by_module:
                tm = self.by_module[b["module"]]
                for key in filter(None, [b["imported"] if b["imported"] != "*" else None, "default"]):
                    exp = tm["exports"].get(key, {})
                    if exp.get("kind") == "function" and exp.get("qual") == ref["method"]:
                        return f"func:{tm['module']}.{exp['qual']}"
                    if not exp:  # CJS: const orders = require('./svc'); orders.createOrder
                        target = next((f for f in tm["functions"]
                                       if f["qual"] == ref["method"] and not f["cls"]), None)
                        if target:
                            return f"func:{tm['module']}.{target['qual']}"
            return None
        name = ref.get("local")
        if not name:
            return None
        for fn in model["functions"]:
            if fn["qual"] == name and not fn["cls"]:
                return f"func:{model['module']}.{name}"
        b = self.bindings[model["rel"]].get(name)
        if b and b.get("module") in self.by_module:
            tm = self.by_module[b["module"]]
            for key in filter(None, [b["imported"] if b["imported"] != "*" else None, "default"]):
                exp = tm["exports"].get(key, {})
                if exp.get("kind") == "function" and exp.get("qual"):
                    return f"func:{tm['module']}.{exp['qual']}"
        return None

    # -- function bodies
    def _analyse_function(self, model: dict, fn: dict):
        fid = f"func:{model['module']}.{fn['qual']}"
        for read in fn["env_reads"]:
            var, line, has_default = read["var"], read["line"], read["has_default"]
            env_id = f"env:{var}"
            self.add_component(env_id, "env_var", var, None, None)
            guard = list(read.get("tries") or [])
            if has_default:
                guard = guard + [[{"names": ["<default>"], "reraises": False, "line": line}]]
            self.add_edge(fid, env_id, "reads_env", "confirmed",
                          self.ev(model, line, "has default value" if has_default else "no default: value is undefined at runtime",
                                  has_default=has_default), guard)
            if not has_default:
                self.env_required.append({"var": var, "component": fid, **self.ev(model, line)})
        for d in fn["db"]:
            self._db_edge(model, fid, d["engine"], d["lib"], d["line"], d.get("tries"),
                          "confirmed" if d.get("kind") == "connect" else "inferred",
                          "()" if d.get("kind") == "connect" else " client call")
        for h in fn["http"]:
            self._http_edge(model, fid, h)
        for line in fn.get("broad_catches", []):
            self.broad_excepts.append({"component": fid, **self.ev(model, line)})
        self._resolve_calls(model, fn, fid, is_test=False)

    def _http_edge(self, model, fid, h):
        if h.get("url_env"):
            label, key = f"External API ({h['url_env']})", h["url_env"]
        elif h.get("url_static") and h["url_static"].startswith(("http://", "https://")):
            host = urlparse(h["url_static"].split("{x}")[0]).hostname or "unknown-host"
            label, key = f"External API ({host})", host
        else:
            label, key = f"External HTTP service ({model['module']})", f"unresolved.{model['module']}"
        ext_id = f"external:{key}"
        self.add_component(ext_id, "external_api", label, None, None, hint=key)
        self.add_edge(fid, ext_id, "calls_http", "confirmed", self.ev(model, h["line"], f"{h['lib']}()"),
                      h.get("tries"))
        self.http_findings.append({"component": fid, "external": ext_id, "lib": h["lib"],
                                   "timeout": bool(h.get("timeout")), "guarded": bool(h.get("guarded")),
                                   "file": model["rel"], "line": h["line"],
                                   "snippet": self.snippet(model, h["line"])})

    def _db_edge(self, model, fid, engine, lib, line, tries, classification, detail_suffix=""):
        if not engine:
            return
        db_id = f"database:{engine}"
        self.add_component(db_id, "database", f"{engine} database", None, None, engine=engine,
                           libraries=sorted(set(self.components.get(db_id, {}).get("data", {}).get("libraries", []))
                                            | {lib.split(".")[0]}))
        self.add_edge(fid, db_id, "uses_database", classification,
                      self.ev(model, line, f"{lib}{detail_suffix}"), tries)

    def _method_id(self, class_id: str, method: str) -> str | None:
        return self.class_methods.get(class_id, {}).get(method)

    def _class_of_parts(self, model: dict, parts: list[str]) -> str | None:
        """Dotted class reference -> class component id (local or imported class)."""
        if not parts:
            return None
        head = parts[0]
        if any(c["name"] == head for c in model["classes"]):
            return f"class:{model['module']}.{head}"
        b = self.bindings[model["rel"]].get(head)
        if b and b.get("module") in self.by_module:
            tm = self.by_module[b["module"]]
            for key in filter(None, [b["imported"] if b["imported"] not in ("default", "*") else None, "default"]):
                exp = tm["exports"].get(key, {})
                if exp.get("kind") == "class" and exp.get("name"):
                    return f"class:{tm['module']}.{exp['name']}"
        return None

    def _resolve_calls(self, model: dict, fn: dict, fid: str, is_test: bool):
        table = self.bindings[model["rel"]]
        local_funcs = {f["qual"] for f in model["functions"] if not f["cls"]}
        for call in fn.get("calls", []):
            p, line = call["parts"], call["line"]
            tries = call.get("tries") or []
            if not p:
                continue
            head = p[0]
            ev = self.ev(model, line)
            # this.method() inside a class
            if head == "this" and fn.get("cls") and len(p) == 2:
                m = self._method_id(f"class:{model['module']}.{fn['cls']}", p[1])
                if m:
                    self.stats["resolved_calls"] += 1
                    self.add_edge(fid, m, "calls", "confirmed", ev, tries)
                    continue
            # instances created locally with new
            ln = next((n for n in fn.get("local_new", []) if n["var"] == head), None)
            if ln and len(p) == 2:
                m = self._method_id(self._class_of_parts(model, ln["parts"]) or "", p[1]) if p[1] else None
                if m:
                    self.stats["resolved_calls"] += 1
                    self.add_edge(fid, m, "calls", "inferred", ev, tries)
                    continue
            # imports
            b = table.get(head)
            if b is not None:
                tm_name = b.get("module")
                if tm_name and tm_name in self.by_module:
                    tm = self.by_module[tm_name]
                    imported = b["imported"]
                    if imported not in ("default", "*") and len(p) == 2:
                        # named import used as an instance: pool.query(...)
                        if self._instance_use(model, tm, tm["exports"].get(imported, {}), p[1], fid, line, ev, tries):
                            continue
                    if imported == "*":
                        if len(p) == 2:
                            exp = tm["exports"].get(p[1], {})
                            if exp.get("kind") == "function" and exp.get("qual"):
                                self._call_edge(fid, f"func:{tm['module']}.{exp['qual']}", "confirmed", ev, tries)
                        continue
                    if imported == "default":
                        dflt = tm["exports"].get("default", {})
                        if len(p) == 1 and dflt.get("kind") == "function" and dflt.get("qual"):
                            self._call_edge(fid, f"func:{tm['module']}.{dflt['qual']}", "confirmed", ev, tries)
                            continue
                        if len(p) == 2:
                            if self._instance_use(model, tm, dflt, p[1], fid, line, ev, tries):
                                continue
                            if dflt.get("kind") == "object" or not dflt:  # CJS: const lib = require('./x'); lib.fn()
                                if self._object_method_call(model, tm, p[1], fid, ev, tries):
                                    continue
                        continue
                    if len(p) == 1:
                        exp = tm["exports"].get(imported, {})
                        if exp.get("kind") == "function" and exp.get("qual"):
                            self._call_edge(fid, f"func:{tm['module']}.{exp['qual']}", "confirmed", ev, tries)
                        continue
                    continue
                if b.get("package"):
                    self.stats["library_calls"] += 1
                    continue
            # module-level instances (db pools, axios instances, class instances)
            inst = model["instances"].get(head)
            if inst and len(p) == 2:
                if inst.get("kind") == "db" and p[1] in DB_INSTANCE_METHODS:
                    self._db_edge(model, fid, inst["engine"], inst["lib"], line, tries, "inferred", " client call")
                    continue
                if inst.get("kind") == "http-instance" and p[1] in HTTP_VERBS | {"request"}:
                    if not model["is_test"]:
                        self._http_edge(model, fid, {"lib": f"{inst['lib']}.{p[1]}", "line": line,
                                                     "timeout": inst.get("timeout"), "guarded": bool(tries),
                                                     "tries": tries, "url_static": None, "url_env": None})
                    continue
                if inst.get("kind") == "new" and inst.get("parts"):
                    m = self._method_id(self._class_of_parts(model, inst["parts"]) or "", p[1]) if p[1] else None
                    if m:
                        self._call_edge(fid, m, "inferred", ev, tries)
                        continue
            # same-module function call
            if head in local_funcs and len(p) == 1:
                self._call_edge(fid, f"func:{model['module']}.{head}", "confirmed", ev, tries)
                continue
            if len(p) == 1 and head not in JS_GLOBALS and head not in model["instances"] \
                    and head not in model["apps"] and head not in model["routers"]:
                self.stats["unresolved_calls"] += 1

    def _call_edge(self, fid, target, classification, ev, tries):
        self.stats["resolved_calls"] += 1
        self.add_edge(fid, target, "calls", classification, ev, tries)

    def _object_method_call(self, model, tm, method: str, fid: str, ev, tries) -> bool:
        """Call a.b() where a is a module's object default export / CJS module."""
        target = next((f for f in tm["functions"] if f["qual"] == method and not f["cls"]), None)
        if target:
            self._call_edge(fid, f"func:{tm['module']}.{target['qual']}", "confirmed", ev, tries)
            return True
        return False

    def _instance_use(self, model, tm, exp: dict, method: str, fid: str, line: int, ev, tries) -> bool:
        """Member call on a value exported from module `tm` (db pool, axios instance, class instance)."""
        if not exp or not method:
            return False
        if exp.get("engine") and method in DB_INSTANCE_METHODS:
            self._db_edge(model, fid, exp["engine"], exp.get("lib") or "instance", line, tries,
                          "inferred", " client call")
            return True
        if exp.get("kind") == "http-instance" and method in HTTP_VERBS | {"request"}:
            if not model["is_test"]:
                self._http_edge(model, fid, {"lib": f"{exp.get('lib', 'http')}.{method}", "line": line,
                                             "timeout": exp.get("timeout"), "guarded": bool(tries), "tries": tries,
                                             "url_static": None, "url_env": None})
            return True
        if exp.get("kind") == "new" and exp.get("parts"):
            m = self._method_id(self._class_of_parts(tm, exp["parts"]) or "", method)
            if m:
                self._call_edge(fid, m, "inferred", ev, tries)
                return True
        return False

    # -- tests
    def _analyse_tests(self):
        routes = [c for c in self.components.values() if c["type"] == "route"]
        for model in self.models.values():
            if not model["is_test"]:
                continue
            tests = list(model["tests"])
            module_http = model.get("_module_http") or []
            if module_http and not tests:
                tests.append({"name": "(module-level)", "line": 1, "calls": [], "http": module_http})
            for t in tests:
                nodeid = f"{model['rel']}::{t['name']}"
                tid = f"test:{nodeid}"
                classname = model["rel"].rsplit(".", 1)[0].replace("/", ".")
                self.add_component(tid, "test", t["name"], model["rel"], t["line"], nodeid=nodeid,
                                   junit_key=f"{classname}::{t['name']}")
                for h in t.get("http", []):
                    tpl = norm_path(h["path"])
                    for r in routes:
                        if (r["data"]["method"] in (h["method"], "ALL")) and paths_match(r["data"]["path"], tpl):
                            self.add_edge(tid, r["id"], "exercises", "inferred",
                                          self.ev(model, h["line"], f"test client {h['method']} {h['path']}"))
                fn = {"qual": t["name"], "cls": None, "calls": t.get("calls", []),
                      "local_new": [], "env_reads": [], "http": [], "db": []}
                self._resolve_calls(model, fn, tid, is_test=True)

    # -- manifests
    def _read_manifests(self, manifests: list[str]) -> list[dict]:
        deps = []
        for rel in manifests:
            if not rel.endswith("package.json"):
                continue
            try:
                data = json.loads((self.root / rel).read_text(encoding="utf-8", errors="replace"))
            except (OSError, ValueError):
                continue
            for section in ("dependencies", "devDependencies"):
                for pkg, spec in (data.get(section) or {}).items():
                    deps.append({"name": pkg, "spec": str(spec), "manifest": rel})
        return deps


def analyse_js_project(root: Path, progress=None, tree: dict | None = None,
                       cache_dir: Path | None = None, use_cache: bool = True) -> dict:
    cache_dir = cache_dir if cache_dir is not None else default_cache_dir(root)
    analyzer = JSAnalyzer(Path(root), tree=tree, cache=FileCache(cache_dir, use_cache=use_cache))
    return analyzer.run(progress)
