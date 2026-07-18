"""Per-language extraction of variable assignments from source text.

Python uses the stdlib ast (exact). Other languages use line-based regex
heuristics (kind values are still precise about what was matched). Each
extractor returns a list of dicts:

    {"name", "line", "scope", "kind", "value"}

scope is a dotted container chain ("Class.method"); "" means module/file
top level. Regex extractors always report scope "" (documented limitation —
the tree-sitter upgrade path in DESIGN.md removes it).
"""
from __future__ import annotations

import ast
import re

# ---------------------------------------------------------------- python ---


def _attr_chain(node: ast.AST) -> str | None:
    """self.x.y -> 'self.x.y' when the chain is Name/Attribute only."""
    parts: list[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        return ".".join(reversed(parts))
    return None


def _target_names(target: ast.AST) -> list[tuple[str, str]]:
    """Yield (name, kind) for an assignment target node."""
    out: list[tuple[str, str]] = []
    if isinstance(target, ast.Name):
        out.append((target.id, "assign"))
    elif isinstance(target, ast.Attribute):
        chain = _attr_chain(target)
        if chain:
            out.append((chain, "attr"))
    elif isinstance(target, (ast.Tuple, ast.List)):
        for elt in target.elts:
            if isinstance(elt, ast.Starred):
                elt = elt.value
            out.extend(_target_names(elt))
    return out


class _PyWalker(ast.NodeVisitor):
    def __init__(self, source: str):
        # precomputed lines: ast.get_source_segment rescans the whole source
        # per call, which goes quadratic on multi-thousand-line files
        self.lines = source.splitlines(keepends=True)
        self.scope: list[str] = []
        self.out: list[dict] = []

    def _scoped(self) -> str:
        return ".".join(self.scope)

    def _value_text(self, node: ast.AST | None) -> str | None:
        if node is None:
            return None
        try:
            l0, c0 = node.lineno - 1, node.col_offset
            l1, c1 = node.end_lineno - 1, node.end_col_offset
            if l0 == l1:
                return self.lines[l0][c0:c1]
            parts = [self.lines[l0][c0:]]
            parts.extend(self.lines[l0 + 1:l1])
            parts.append(self.lines[l1][:c1])
            return "".join(parts)
        except (AttributeError, IndexError):
            return None

    def _emit(self, name: str, kind: str, line: int, value: ast.AST | None,
              anno: ast.AST | None = None):
        self.out.append({
            "name": name,
            "line": line,
            "scope": self._scoped(),
            "kind": kind,
            "value": self._value_text(value),
            "anno": self._value_text(anno),
        })

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def visit_ClassDef(self, node: ast.ClassDef):
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_Assign(self, node: ast.Assign):
        for target in node.targets:
            for name, kind in _target_names(target):
                self._emit(name, kind, node.lineno, node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign):
        if node.value is not None:  # 'x: int' alone declares, doesn't assign
            for name, _ in _target_names(node.target):
                self._emit(name, "annassign", node.lineno, node.value,
                           anno=node.annotation)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign):
        for name, _ in _target_names(node.target):
            self._emit(name, "augassign", node.lineno, node.value)
        self.generic_visit(node)

    def visit_NamedExpr(self, node: ast.NamedExpr):
        if isinstance(node.target, ast.Name):
            self._emit(node.target.id, "walrus", node.lineno, node.value)
        self.generic_visit(node)


def extract_python(source: str) -> list[dict]:
    tree = ast.parse(source)
    walker = _PyWalker(source)
    walker.visit(tree)
    return walker.out


# ------------------------------------------------------------ regex langs ---

_JS_KEYWORDS = {
    "if", "for", "while", "switch", "return", "typeof", "await", "case",
    "default", "new", "delete", "void", "in", "of", "do", "else", "try",
    "catch", "finally", "throw", "yield", "class", "function", "import",
    "export", "this", "super", "true", "false", "null", "undefined",
}


def _strip_line_comment(line: str, markers: tuple[str, ...]) -> str:
    # string-aware: a comment marker inside a quoted string is NOT a comment.
    # Honors ' " ` quoting and backslash escapes, then cuts at the first marker
    # found outside a string. Without this the naive `//` cut truncated every
    # URL constant at its scheme — `const U = "https://x"` became `"https:` —
    # collapsing distinct URLs to one shared value (dogfood 2026-07-17,
    # greenyogainc: GITHUB_URL/OPENVSX_URL both stored as "https:").
    quote = None
    i, n = 0, len(line)
    while i < n:
        ch = line[i]
        if quote is not None:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "'\"`":
            quote = ch
            i += 1
            continue
        for m in markers:
            if line.startswith(m, i):
                return line[:i]
        i += 1
    return line


def _split_top_level(s: str, sep: str = ",") -> list[str]:
    parts, depth, buf = [], 0, []
    for ch in s:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == sep and depth <= 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return parts


def _partition_top_level(s: str, sep: str = "=") -> tuple[str, str, str]:
    """Like str.partition but only splits at bracket-depth 0 (and not on
    '==', '=>', '<=', '>=', '!=', '+=' style operators)."""
    depth = 0
    for i, ch in enumerate(s):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == sep and depth == 0:
            prev = s[i - 1] if i > 0 else ""
            nxt = s[i + 1] if i + 1 < len(s) else ""
            if prev in "=!<>+-*/%&|^" or nxt in "=>":
                continue
            return s[:i], sep, s[i + 1:]
    return s, "", ""


def _destructured_names(pattern: str) -> list[str]:
    """{a, b: alias, c = 1} or [x, , y] -> local binding names."""
    inner = pattern.strip()
    if inner and inner[0] in "{[":
        closer = "}" if inner[0] == "{" else "]"
        end = inner.rfind(closer)  # drops TS ': Type' suffix after the pattern
        inner = inner[1:end] if end != -1 else inner[1:]
    names = []
    for part in _split_top_level(inner):
        part = part.strip().lstrip(".")  # rest element ...tail
        if not part:
            continue
        if ":" in part:  # {orig: alias} binds alias
            part = part.split(":", 1)[1]
        part = part.split("=", 1)[0].strip()  # default value
        m = re.match(r"^[A-Za-z_$][\w$]*$", part)
        if m:
            names.append(part)
        elif part and part[0] in "{[":  # nested destructuring
            names.extend(_destructured_names(part))
    return names


class _BraceScopes:
    """Line-based function-scope tracker for brace languages (JS/TS, Go, C#,
    Java, Rust). Counts braces naively (strings/comments not honored — the
    file-wide 'acceptable for heuristics' rule) and attributes captures to the
    innermost enclosing function, so component/method locals stop being
    mistaken for module-level concepts and never pair across files."""

    def __init__(self, openers: list) -> None:
        self.openers = openers          # compiled regexes with a 'name' group
        self.depth = 0
        self.stack: list[tuple[int, str]] = []  # (entry depth, function name)
        self._pending: str | None = None        # Allman style: '{' on next line

    def scope(self) -> str:
        return self.stack[-1][1] if self.stack else ""

    def feed(self, line: str) -> None:
        name = None
        for rx in self.openers:
            m = rx.match(line)
            if m:
                name = m.group("name")
                break
        opens, closes = line.count("{"), line.count("}")
        if self._pending is not None and opens > 0:
            self.stack.append((self.depth, self._pending))
            self._pending = None
        elif name:
            if opens > 0:
                self.stack.append((self.depth, name))
            elif not line.rstrip().endswith(";"):
                self._pending = name  # brace expected on a following line
        self.depth += opens - closes
        while self.stack and self.depth <= self.stack[-1][0]:
            self.stack.pop()


_JS_FUNC_OPENERS = [
    re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*"
               r"(?P<name>[A-Za-z_$][\w$]*)"),
    # const Foo = (...) => / const Foo = async function
    re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)"
               r"[^=]*=\s*(?:async\s+)?(?:function\b|\([^)]*\)\s*(?::[^=>{]+)?=>)"),
]

# A JSX tag opened but not closed on the same line: its following lines are
# attributes (className= / onClick= / stroke=), NOT assignments.
_JSX_TAG_OPEN = re.compile(r"<[A-Za-z][\w.]*(?:\s[^<>]*)?$")
_JSX_TAG_CLOSE = re.compile(r"(?<!=)>")  # a real '>', not the '=>' arrow

_JS_DECL = re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(const|let|var)\s+(.*)$")
_JS_BARE = re.compile(
    r"^\s*([A-Za-z_$][\w$]*(?:\.[\w$]+)*)\s*=(?![=>])\s*(.+?);?\s*$"
)


def extract_js(source: str) -> list[dict]:
    out: list[dict] = []
    scopes = _BraceScopes(_JS_FUNC_OPENERS)
    in_jsx_tag = False
    for i, raw in enumerate(source.splitlines(), start=1):
        line = _strip_line_comment(raw, ("//",))
        if not line.strip() or line.strip().startswith(("*", "/*")):
            continue
        if in_jsx_tag:
            # attribute lines of a multi-line JSX tag — never assignments
            if _JSX_TAG_CLOSE.search(line):
                in_jsx_tag = False
            scopes.feed(line)
            continue
        if _JSX_TAG_OPEN.search(line):
            in_jsx_tag = True
            scopes.feed(line)
            continue
        scope = scopes.scope()
        m = _JS_DECL.match(line)
        if m:
            kw, rest = m.group(1), m.group(2)
            for decl in _split_top_level(rest):
                decl = decl.strip().rstrip(";")
                if not decl:
                    continue
                lhs, eq, rhs = _partition_top_level(decl, "=")
                lhs = lhs.strip()
                # strip TS type annotation on simple declarators
                ts = lhs.split(":", 1)[0].strip() if not lhs.startswith(("{", "[")) else lhs
                if ts.startswith(("{", "[")):
                    for name in _destructured_names(ts):
                        out.append({"name": name, "line": i, "scope": scope,
                                    "kind": f"{kw}-destructure",
                                    "value": rhs.strip() or None})
                elif re.match(r"^[A-Za-z_$][\w$]*$", ts):
                    if not eq:
                        continue  # bare 'let x;' declares, doesn't assign
                    out.append({"name": ts, "line": i, "scope": scope,
                                "kind": kw, "value": rhs.strip() or None})
            scopes.feed(line)
            continue
        b = _JS_BARE.match(line)
        if b:
            name = b.group(1)
            head = name.split(".", 1)[0]
            if head in _JS_KEYWORDS or head in ("const", "let", "var"):
                scopes.feed(line)
                continue
            rhs = b.group(2).strip()
            if rhs.startswith("="):  # '==' comparison slipped through
                scopes.feed(line)
                continue
            out.append({"name": name, "line": i, "scope": scope,
                        "kind": "reassign", "value": rhs})
        scopes.feed(line)
    return out


_PS_ASSIGN = re.compile(
    r"^\s*\$(?:(global|script|env|local|private):)?([A-Za-z_][\w]*)\s*=(?!=)\s*(.+?)\s*$"
)
_PS_SETVAR = re.compile(
    r"(?i)^\s*Set-Variable\s+(?:-Name\s+)?['\"]?([A-Za-z_]\w*)['\"]?"
    r"(?:\s+(?:-Value\s+)?(.+?))?\s*$"
)


def extract_powershell(source: str) -> list[dict]:
    out = []
    in_herestring = False
    for i, raw in enumerate(source.splitlines(), start=1):
        if in_herestring:
            if raw.lstrip().startswith(("'@", '"@')):
                in_herestring = False
            continue
        if raw.rstrip().endswith(("@'", '@"')):
            in_herestring = True  # here-string body is data, not code
        line = _strip_line_comment(raw, ("#",))
        m = _PS_ASSIGN.match(line)
        if m:
            prefix, name, rhs = m.groups()
            kind = f"ps-{prefix}" if prefix else "ps-assign"
            out.append({"name": name, "line": i, "scope": "",
                        "kind": kind, "value": rhs})
            continue
        m = _PS_SETVAR.match(line)
        if m:
            out.append({"name": m.group(1), "line": i, "scope": "",
                        "kind": "ps-setvariable", "value": m.group(2)})
    return out


_SH_ASSIGN = re.compile(
    r"^\s*(?:(export|local|readonly|declare(?:\s+-[A-Za-z]+)?)\s+)?"
    r"([A-Za-z_]\w*)=(.*)$"
)
_SH_HEREDOC = re.compile(r"<<-?\s*['\"]?([A-Za-z_]\w*)['\"]?")


def extract_shell(source: str) -> list[dict]:
    out = []
    heredoc_end = None
    for i, raw in enumerate(source.splitlines(), start=1):
        if heredoc_end is not None:
            # heredoc body is file content being written, not shell code
            # (dogfood: AWS credentials-file keys were captured as variables)
            if raw.strip() == heredoc_end:
                heredoc_end = None
            continue
        line = _strip_line_comment(raw, ("#",))
        m = _SH_ASSIGN.match(line)
        if m:
            kw, name, rhs = m.groups()
            out.append({"name": name, "line": i, "scope": "",
                        "kind": f"sh-{kw.split()[0]}" if kw else "sh-assign",
                        "value": rhs.strip() or None})
        h = _SH_HEREDOC.search(line)
        if h:
            heredoc_end = h.group(1)
    return out


_ENV_ASSIGN = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][\w.]*)\s*=\s*(.*)$")


def extract_env(source: str) -> list[dict]:
    out = []
    for i, raw in enumerate(source.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _ENV_ASSIGN.match(line)
        if m:
            out.append({"name": m.group(1), "line": i, "scope": "",
                        "kind": "env", "value": m.group(2).strip() or None})
    return out


_CS_KEYBLOCK = (
    r"(?:public|private|protected|internal|static|readonly|const|volatile|new|override|sealed"
    r"|var|int|uint|long|ulong|short|byte|bool|string|double|float|decimal|char|object|dynamic)"
)
_CS_ASSIGN = re.compile(
    rf"^\s*(?:{_CS_KEYBLOCK}\s+)+([A-Za-z_]\w*)\s*=(?!=)\s*(.+?);\s*$"
)
# method-shaped line: optional modifiers, a return type, Name(...), no ';'
# (an abstract declaration or a call statement ends with ';'). Class-level
# fields keep scope "" — they ARE cross-file concepts; method locals don't.
_CS_METHOD_OPENER = re.compile(
    r"^\s*(?:(?:public|private|protected|internal|static|async|override|virtual"
    r"|sealed|abstract|new|partial|final|synchronized)\s+)+[\w<>\[\],\.\?\s]+?\s+"
    r"(?P<name>[A-Za-z_]\w*)\s*\([^;]*\)?\s*(?:\{\s*)?$"
)


def extract_csharp(source: str) -> list[dict]:
    out = []
    scopes = _BraceScopes([_CS_METHOD_OPENER])
    for i, raw in enumerate(source.splitlines(), start=1):
        line = _strip_line_comment(raw, ("//",))
        m = _CS_ASSIGN.match(line)
        if m:
            out.append({"name": m.group(1), "line": i, "scope": scopes.scope(),
                        "kind": "cs-assign", "value": m.group(2)})
        scopes.feed(line)
    return out


_GO_SHORT = re.compile(r"^\s*([A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)\s*:=\s*(.+)$")
_GO_VAR = re.compile(r"^\s*var\s+([A-Za-z_]\w*)(?:\s+[\w\[\]\*\.]+)?\s*=\s*(.+)$")
_GO_FUNC_OPENER = re.compile(r"^func\s+(?:\([^)]*\)\s*)?(?P<name>\w+)")


def extract_go(source: str) -> list[dict]:
    out = []
    scopes = _BraceScopes([_GO_FUNC_OPENER])
    for i, raw in enumerate(source.splitlines(), start=1):
        line = _strip_line_comment(raw, ("//",))
        m = _GO_SHORT.match(line)
        if m:
            for name in (n.strip() for n in m.group(1).split(",")):
                if name != "_":
                    out.append({"name": name, "line": i,
                                "scope": scopes.scope(),
                                "kind": "go-short", "value": m.group(2).strip()})
            scopes.feed(line)
            continue
        m = _GO_VAR.match(line)
        if m:
            out.append({"name": m.group(1), "line": i, "scope": scopes.scope(),
                        "kind": "go-var", "value": m.group(2).strip()})
        scopes.feed(line)
    return out


# ------------------------------------------------------------------- rust ---

_RUST_LET = re.compile(
    r"^\s*let\s+(?:mut\s+)?(?P<name>[A-Za-z_]\w*)(?:\s*:\s*[^=]+)?\s*=\s*(?P<value>.+?);?\s*$"
)
_RUST_CONST = re.compile(
    r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?P<kw>const|static)\s+(?:mut\s+)?"
    r"(?P<name>[A-Za-z_]\w*)\s*:\s*[^=]+=\s*(?P<value>.+?);\s*$"
)
_RUST_FN_OPENER = re.compile(
    r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:unsafe\s+)?(?:extern\s+\"[^\"]*\"\s+)?"
    r"fn\s+(?P<name>\w+)"
)


def extract_rust(source: str) -> list[dict]:
    out = []
    scopes = _BraceScopes([_RUST_FN_OPENER])
    for i, raw in enumerate(source.splitlines(), start=1):
        line = _strip_line_comment(raw, ("//",))
        m = _RUST_CONST.match(line)
        if m:
            out.append({"name": m.group("name"), "line": i,
                        "scope": scopes.scope(),
                        "kind": f"rust-{m.group('kw')}",
                        "value": m.group("value").strip()})
            scopes.feed(line)
            continue
        m = _RUST_LET.match(line)
        if m:
            out.append({"name": m.group("name"), "line": i,
                        "scope": scopes.scope(),
                        "kind": "rust-let", "value": m.group("value").strip()})
        scopes.feed(line)
    return out


# ------------------------------------------------------------------- java ---

_JAVA_KEYBLOCK = (
    r"(?:public|private|protected|static|final|transient|volatile"
    r"|var|int|long|short|byte|boolean|double|float|char|String|Object)"
)
_JAVA_ASSIGN = re.compile(
    rf"^\s*(?:{_JAVA_KEYBLOCK}\s+)+(?:[\w<>\[\],\.\?\s]+?\s+)?"
    rf"(?P<name>[A-Za-z_]\w*)\s*=(?!=)\s*(?P<value>.+?);\s*$"
)
_JAVA_METHOD_OPENER = _CS_METHOD_OPENER  # same modifier/return-type shape


def extract_java(source: str) -> list[dict]:
    out = []
    scopes = _BraceScopes([_JAVA_METHOD_OPENER])
    for i, raw in enumerate(source.splitlines(), start=1):
        line = _strip_line_comment(raw, ("//",))
        m = _JAVA_ASSIGN.match(line)
        if m:
            out.append({"name": m.group("name"), "line": i,
                        "scope": scopes.scope(),
                        "kind": "java-assign", "value": m.group("value").strip()})
        scopes.feed(line)
    return out


# ---------------------------------------------------------------- registry ---

EXTRACTORS: dict[str, tuple[str, callable]] = {
    ".py": ("python", extract_python),
    ".pyw": ("python", extract_python),
    ".js": ("javascript", extract_js),
    ".jsx": ("javascript", extract_js),
    ".mjs": ("javascript", extract_js),
    ".cjs": ("javascript", extract_js),
    ".ts": ("typescript", extract_js),
    ".tsx": ("typescript", extract_js),
    ".ps1": ("powershell", extract_powershell),
    ".psm1": ("powershell", extract_powershell),
    ".sh": ("shell", extract_shell),
    ".bash": ("shell", extract_shell),
    ".cs": ("csharp", extract_csharp),
    ".go": ("go", extract_go),
    ".rs": ("rust", extract_rust),
    ".java": ("java", extract_java),
    ".env": ("env", extract_env),
}


def lang_for_path(path: str) -> str | None:
    p = path.lower()
    name = p.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if name == ".env" or name.startswith(".env."):
        return "env"
    dot = name.rfind(".")
    if dot == -1:
        return None
    ext = name[dot:]
    entry = EXTRACTORS.get(ext)
    return entry[0] if entry else None


def extract(path: str, source: str) -> tuple[str | None, list[dict]]:
    """Returns (lang, variables). Unsupported extension -> (None, [])."""
    lang = lang_for_path(path)
    if lang is None:
        return None, []
    fn = None
    for _, (lg, f) in EXTRACTORS.items():
        if lg == lang:
            fn = f
            break
    if lang == "env":
        fn = extract_env
    if fn is None:
        return None, []
    try:
        return lang, fn(source)
    except SyntaxError:
        return lang, []  # half-written file mid-session: skip quietly
    except Exception:
        return lang, []
