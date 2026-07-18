"""Duplicate / alignment detection over the variable registry.

The LLM failure mode this targets: one session names a concept MAX_RETRIES,
a later session (or a second agent) introduces RETRY_LIMIT — or re-declares
the same name elsewhere with a different value. Signals, strongest first:

  name-variant   same normalized name, different location   (maxConn / MAX_CONN)
  value-mismatch same top-level name, different value        (alignment risk)
  same-value     different names, identical non-trivial RHS  (one constant, two names)
  token-overlap  related names by stemmed-token Jaccard      (MAX_RETRIES / RETRY_LIMIT)

Pairs are bucketed (never O(N^2)), scored 0..1, leveled high/medium/low, and
filtered through dup_reviews so a 'not_duplicate' verdict stays dismissed.
"""
from __future__ import annotations

import os
import re
from collections import defaultdict
from pathlib import Path

from .store import pair_key as _pair_key

LEVELS = [("high", 0.75), ("medium", 0.55), ("low", 0.35)]

# canonical severity ordering — shared by the CLI and the prompt generator so
# the constant lives in exactly one place (previously copied into both).
LEVEL_ORDER = {"high": 3, "medium": 2, "low": 1}

# reasons whose "relatedness" is a shared token set — the only ones from which
# a dismissal generalizes into a learned family-suppression rule (see
# find_duplicates). Name/value-identity reasons stay per-pair (exact).
_FAMILY_REASONS = ("token-overlap", "shared-token")

# same-name reasons: dismissing ONE className<->className pair means "this
# name legitimately repeats" — the verdict generalizes to every pair of that
# normalized name, instead of forcing one dismissal per pair (dogfood
# 2026-07-16: 108-pair React flood needed one click, not 108)
_SAMENAME_REASONS = ("value-mismatch", "same-name", "same-name-same-value")

TRIVIAL_VALUES = {
    "", "0", "1", "-1", "true", "false", "none", "null", "nil",
    "[]", "{}", "()", '""', "''", "``", "0.0", "1.0", "[];", "{};",
}

# Container constructors that produce an *empty* collection: seeded with a bare
# builtin factory they are the same "empty accumulator" idiom as [] / {} —
# `defaultdict(list)` says no more about the concept than `[]` does, so it must
# not anchor same-value pairing (see _nontrivial_value). Compared lowercased.
_CONTAINER_CTORS = {"defaultdict", "dict", "list", "set", "tuple", "deque",
                    "ordereddict", "counter", "frozenset"}
_TRIVIAL_FACTORIES = {"list", "dict", "set", "tuple", "int", "float", "str",
                      "bool", "bytes", "complex", "frozenset", "deque",
                      "ordereddict", "counter"}

# idiomatic per-module names that legitimately repeat everywhere
DEFAULT_IGNORE = {
    "logger", "log", "app", "args", "parser", "router", "client", "conn",
    "cursor", "session", "db", "e", "err", "error", "result", "res", "data",
    "response", "req", "request", "ctx", "config", "cfg", "settings", "main",
    "i", "j", "k", "x", "y", "_", "self", "cls", "path", "name", "value",
    "out", "output", "line", "row", "rows", "item", "items", "key", "keys",
    "f", "s", "p", "v", "r", "m", "n", "c", "t",
    # ultra-generic transient locals (dogfood 2026-07-16): reused everywhere
    # under the same bare name, so a same-name match is coincidence not drift
    "text", "url", "uri", "content", "body", "payload", "msg", "message",
    "tmp", "temp", "buf", "buffer", "obj", "elem", "element", "node", "idx",
    "index", "count", "total", "val", "params", "opts", "options", "resp",
    "ret", "block", "doc", "css", "html", "page", "template", "style", "styles",
    "code", "mod", "lic",
    # universal DOM/JSX prop names (dogfood 2026-07-16, React repos): these are
    # element attributes, not concepts — the JSX-aware extractor no longer
    # captures them, but old stores and other markup-ish langs still can
    "classname", "onclick", "onchange", "onsubmit", "onclose", "onfocus",
    "onblur", "disabled", "checked", "placeholder", "htmlfor", "children",
    "isloading", "onselect", "oninput", "onkeydown", "onmouseover",
    # SVG/HTML presentation attributes + link/media props (dogfood 2026-07-17,
    # greenyogainc: strokeLinecap="round" / strokeLinejoin="round" flooded HIGH
    # across every icon, and href value-mismatch fired on every distinct link).
    # Sub-3-char ones (d, cx, cy, rx, ry, x1…) are already dropped by the
    # len<3 rule in _eligible_pair, so only the >=3-char attrs are listed.
    # DELIBERATELY attribute-only names: dual-use words that are also ordinary
    # variables (transform, loading, offset, points, src, alt, rel, sizes,
    # stroke, fill, decoding) are NOT here — blanket-ignoring them would mask a
    # real same-name drift of that variable (both sides ignored => scored None).
    "strokelinecap", "strokelinejoin", "strokewidth", "strokedasharray",
    "strokedashoffset", "strokeopacity", "fillrule", "fillopacity", "cliprule",
    "viewbox", "xmlns", "stopcolor", "gradienttransform", "preserveaspectratio",
    "href", "srcset", "tabindex", "colspan", "rowspan", "maxlength",
    "minlength", "autocomplete", "spellcheck", "draggable", "contenteditable",
    # idiomatic framework field/export names that legitimately repeat across
    # unrelated domains: Next.js `export const metadata`, Stripe's
    # `paymentIntent.metadata`, etc. (dogfood 2026-07-17, greenyogainc D8)
    "metadata",
}

# tokens that carry structure but no domain meaning. When two compound names
# share ONLY these, the overlap is scaffolding coincidence — FAVICON_DATA_URI /
# LOGO_DATA_URI share {data,uri}; LEGACY_DIRNAME / VARMEM_DIRNAME share
# {dirname} — not a shared concept. Kept deliberately narrow so real families
# (MAX_RETRIES/RETRY_LIMIT, ISSUES_PAGE_SIZE/VARS_PAGE_SIZE) still pair on their
# meaningful token (retry, page+size are intentionally NOT in here).
GENERIC_TOKENS = frozenset({
    "data", "uri", "url", "id", "key", "name", "value", "val", "type", "info",
    "item", "obj", "str", "idx", "index", "flag", "opt", "option", "param",
    "arg", "ctx", "path", "dir", "dirname", "file", "filename", "msg", "text",
    "content", "body", "tmp", "temp", "buf", "buffer", "ret", "resp", "attr",
    "prop", "field", "meta", "default", "base", "node", "elem", "element",
})

_CAMEL = re.compile(r"([a-z0-9])([A-Z])")
_WORD = re.compile(r"[A-Za-z]+|\d+")

# Deliberately-symmetric antonym token pairs: two names identical except for
# one of these differ by DESIGN, not drift (RANK_INPUT_WEIGHT/RANK_OUTPUT_WEIGHT,
# prompt/completion_usd_per_mtok, OPENROUTER_BYOK_FEE_PCT/…_PAYG_FEE_PCT). Kept
# to the evidenced set only (dogfood 2026-07-16, ai-models-pricing) and applied
# as a same-file damper (sink a tier), never an exclusion — a cross-file
# MIN_X/MAX_X drift is never damped and a same-file one stays visible at low.
_ANTONYMS = frozenset({
    frozenset(("input", "output")), frozenset(("min", "max")),
    frozenset(("prompt", "completion")), frozenset(("byok", "payg")),
})

# format-variant twins: one concept rendered two ways (…Html/…Text,
# …Json/…Yaml) — a deliberate parallel pair declared together, not drift.
# Damped like antonyms (dogfood 2026-07-17, greenyogainc D12:
# licenseBlocksHtml/licenseBlocksText — the HTML+text halves of one email).
_FORMAT_VARIANTS = frozenset({
    frozenset(("html", "text")), frozenset(("html", "plain")),
    frozenset(("html", "txt")), frozenset(("json", "yaml")),
    frozenset(("json", "text")), frozenset(("html", "markdown")),
    frozenset(("html", "md")),
})

# React/hook setter+handler leading tokens: setPageSize / changePageSize / the
# `pageSize` state var are the useState idiom (getter/setter/handler family),
# not drifting concepts — same rationale as the DEFAULT_IGNORE JSX-prop entries,
# generalized. (dogfood 2026-07-16, ai-models-pricing/ModelTable.tsx)
_HOOK_PREFIXES = frozenset({"set", "change"})

# field-kind SUFFIX tokens that name the KIND of a config field, not its
# concept: a class of sibling secret fields (anthropic_api_key / openai_api_key
# / …_admin_key) differs only by provider/qualifier, so the pairwise overlap is
# a family shape, not a collision. (dogfood 2026-07-16, ai-models-pricing)
_CREDENTIAL_SUFFIX = frozenset({"key", "token", "secret", "password",
                                "credential"})


def norm_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _is_dunder(name: str) -> bool:
    # module/class metadata idioms (__all__, __version__, __author__, __slots__)
    # legitimately repeat in every module — never a shared concept (dogfood
    # 2026-07-16, a production repo: __all__ paired 148x across agent modules)
    return bool(re.fullmatch(r"__\w+__", name))


def _stem(tok: str) -> str:
    if tok.endswith("ies") and len(tok) > 4:
        return tok[:-3] + "y"
    if tok.endswith("es") and len(tok) > 4:
        return tok[:-2]
    if tok.endswith("s") and len(tok) > 3:
        return tok[:-1]
    if tok.endswith("ing") and len(tok) > 5:
        return tok[:-3]
    if tok.endswith("ed") and len(tok) > 4:
        return tok[:-2]
    return tok


def name_tokens(name: str) -> frozenset[str]:
    spaced = _CAMEL.sub(r"\1 \2", name.replace(".", " "))
    toks = set()
    for w in _WORD.findall(spaced):
        w = w.lower()
        if w in ("self", "cls", "this"):
            continue  # receiver names, not concept tokens (every attr has one)
        if len(w) >= 2 and not w.isdigit():
            toks.add(_stem(w))
    return frozenset(toks)


def _ordered_tokens(name: str) -> list[str]:
    """Lowercased word tokens in source order (unlike name_tokens, which stems
    and de-dups into a set) — for first/last-token family checks."""
    spaced = _CAMEL.sub(r"\1 \2", name.replace(".", " "))
    return [w.lower() for w in _WORD.findall(spaced) if not w.isdigit()]


# ORM/dataclass/settings column-or-field declarations are type+constraint
# scaffolding, not values: `mapped_column(Float, nullable=False)` repeats on
# every column of a kind and `Field(default="")` on every settings field, so a
# shared one must never anchor same-value pairing. Only a literal `default=` /
# `server_default=` inside them can carry a meaningful shared value.
_DECL_CALL = re.compile(
    r"(?:[a-z_]\w*\.)*(?:mapped_column|column|field)\s*\((?P<args>.*)\)", re.S)


def _declaration_literal(v: str) -> str | None:
    """Reduce a column/field declaration to the literal it defaults to.

    Returns the `default=`/`server_default=` literal, "" when the declaration
    carries no literal default (pure type+constraint boilerplate, or a
    default_factory reference — both trivial), or None when v is not such a
    declaration, so ordinary values are judged unchanged. (dogfood 2026-07-16,
    ai-models-pricing: sibling `mapped_column(Float, nullable=False)` columns
    paired as HIGH same-value; `Field(default="postgres://…")` must still pair.)
    """
    m = _DECL_CALL.fullmatch(v)
    if not m:
        return None
    inner = m.group("args")
    if re.search(r"\bdefault_factory\s*=", inner):
        return ""  # factory reference, not a literal value
    dm = re.search(r"\b(?:server_)?default\s*=\s*", inner)
    if not dm:
        return ""  # type+constraint scaffolding only — no meaningful value
    depth, buf = 0, []
    for ch in inner[dm.end():]:  # capture the literal up to its top-level comma
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            if depth == 0:
                break
            depth -= 1
        elif ch == "," and depth == 0:
            break
        buf.append(ch)
    return "".join(buf).strip()


def _nontrivial_value(r) -> bool:
    v = (r["value_preview"] or "").strip().lower()
    if r["redacted"]:
        return True  # redacted values are by definition interesting
    # line-based extractors keep a trailing separator on a captured RHS (a
    # destructured `numeric = false,` default-param stores "false,"); strip it
    # so `false,`/`0,`/`"",` collapse to their bare trivial form before the
    # test below (dogfood 2026-07-16, ai-models-pricing: center/numeric="false,"
    # paired as same-value because "false," is neither trivial nor len < 4)
    v = v.rstrip(" ,;")
    # framework column/field declarations wrap the real (or absent) value —
    # judge only the literal default, so `mapped_column(Float, nullable=False)`
    # and `Field(default="")` never anchor same-value pairing while
    # `Field(default="postgres://db/prod")` still does
    lit = _declaration_literal(v)
    if lit is not None:
        v = lit
    if v in TRIVIAL_VALUES or len(v) < 4:
        return False
    if re.fullmatch(r"-?\d{1,6}(\.\d+)?", v):
        return False  # bare numbers up to 6 digits (ports 48022, char caps
        # 4000, sizes 65536) coincide across unrelated names — not a shared
        # value (dogfood 2026-07-17, a production repo: MAX_OUTPUT_CHARS /
        # MAX_DESCRIPTION_CHARS both 4000 paired HIGH same-value)
    # a bare alias (do_get = _dispatch) or a no-argument constructor/call
    # (threading.lock(), io.stringio(), event()) recurs across unrelated names
    # by idiom — it is not a meaningful shared value for pairing
    if re.fullmatch(r"[a-z_][\w.]*(?:\(\))?", v):
        return False
    # a container constructor seeded with a bare builtin factory —
    # defaultdict(list), defaultdict(int), OrderedDict(list) — is the same
    # empty-accumulator idiom as [] / {} / dict(): the element type of an empty
    # container carries no dedup signal, so it must not anchor same-value
    # pairing either (dogfood 2026-07-17, a production repo: by_tool / signature_groups,
    # both `defaultdict(list)`, paired same-value even after a deliberate
    # disambiguating rename). A non-factory arg (dict(user_cfg), defaultdict(
    # make_widget)) is left interesting — that carries a real shared value.
    m = re.fullmatch(r"([a-z_][\w.]*)\(\s*([a-z_][\w.]*)\s*\)", v)
    if m and m.group(1).rsplit(".", 1)[-1] in _CONTAINER_CTORS \
            and m.group(2).rsplit(".", 1)[-1] in _TRIVIAL_FACTORIES:
        return False
    # a value captured as a truncated multi-line call/collection — the RHS
    # opened `(` / `[` / `{` that continues on the next line, so only the head
    # was stored — is not a meaningful shared value: every multi-line
    # `createFileSystemWatcher(` / `useState(` / `[` collides on the head alone.
    # (dogfood 2026-07-18, varmem: srcWatcher / watcher both stored as
    # `vscode.workspace.createfilesystemwatcher(`.)
    if v.endswith(("(", "[", "{")):
        return False
    return True


def _dir_of(file: str) -> str:
    return file.rsplit("/", 1)[0] if "/" in file else ""


# committed seed templates that mirror the real .env key-for-key by design.
_ENV_TEMPLATE_MARKERS = (".example", ".sample", ".template", ".dist",
                         ".defaults", ".tpl")


def _is_env_template(file: str) -> bool:
    base = file.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].lower()
    return base.startswith(".env") and any(
        mk in base for mk in _ENV_TEMPLATE_MARKERS)


def _unit_key(file: str, patterns: list[str]) -> str | None:
    """The standalone unit a file belongs to, or None if it matches no pattern.

    A pattern is a '/'-separated path prefix in which '*' matches exactly one
    path segment and becomes part of the unit identity. The matched prefix must
    be a proper DIRECTORY of the file (the file lives inside it), so a bare
    filename never becomes a unit: "lambda/*" puts lambda/api/x.ts in unit
    "lambda/api" and lambda/web/y.ts in "lambda/web"; "*" makes each top-level
    directory its own unit. The most specific (longest) matching pattern wins.
    """
    segs = file.replace("\\", "/").split("/")
    best: tuple[int, str] | None = None
    for pat in patterns:
        pseg = pat.strip("/").split("/")
        if not pseg or len(pseg) >= len(segs):
            continue  # unit must be a directory prefix, file lives beneath it
        if all(ps == "*" or ps == segs[i] for i, ps in enumerate(pseg)):
            if best is None or len(pseg) > best[0]:
                best = (len(pseg), "/".join(segs[:len(pseg)]))
    return best[1] if best else None


# deploy/package manifests that mark an independently-shippable subtree.
_UNIT_MANIFESTS = frozenset({
    "package.json", "pyproject.toml", "setup.py", "go.mod", "cargo.toml",
    "wrangler.toml", "wrangler.jsonc", "serverless.yml", "serverless.yaml",
    "dockerfile", "pom.xml", "build.gradle", "composer.json", "gemfile",
})


def _auto_unit_patterns(root, exclude_dirs) -> list[str]:
    """['*'] when the repo is a multi-component layout — >=2 top-level
    directories that each contain a deploy/package manifest beneath them — else
    []. Such subtrees ship independently and legitimately copy constants across
    runtimes (the license key / grace-days replicated in an edge worker + a VS
    Code extension + a signing lib), so each top-level dir becomes a standalone
    unit and cross-unit pairs are suppressed with no config. A single root
    manifest describes the whole repo and never triggers this. Bounded and
    best-effort; any error just disables the heuristic. (dogfood 2026-07-17.)"""
    try:
        base = Path(root)
        tops: set[str] = set()
        for dirpath, dirnames, filenames in os.walk(base):
            rel = Path(dirpath).relative_to(base).as_posix()
            depth = 0 if rel in ("", ".") else rel.count("/") + 1
            dirnames[:] = [] if depth >= 4 else [
                d for d in dirnames
                if d not in exclude_dirs and not d.startswith(".")]
            if rel in ("", "."):
                continue  # a root manifest describes the whole repo, not a unit
            if any(f.lower() in _UNIT_MANIFESTS for f in filenames):
                tops.add(rel.split("/")[0])
                if len(tops) >= 2:
                    return ["*"]
        return []
    except Exception:
        return []


def _eligible_pair(a, b, unit_keys=None) -> bool:
    if a["id"] == b["id"]:
        return False
    # standalone units: two sides in DIFFERENT declared isolated units are
    # independent by architecture — a same-name/same-value or value-mismatch
    # across them is expected copy-paste, not drift (they can't be deduped into
    # one module). Suppress only when BOTH sit in a declared unit and the units
    # differ; a file outside every unit pairs normally. (dogfood 2026-07-17,
    # a repo with standalone-deployable handlers.)
    if unit_keys is not None:
        ua, ub = unit_keys.get(a["id"]), unit_keys.get(b["id"])
        if ua is not None and ub is not None and ua != ub:
            return False
    # .env.example / .env.sample / … are documentation seeds that mirror the
    # live .env key-for-key; a mirrored key (real value vs empty placeholder,
    # or an identical non-secret default) is the template design, not a
    # duplicate concept (dogfood 2026-07-17, a production repo: ~25 of 60
    # findings were this .env/.env.example mirror). Same-dir env pair where
    # either side is a template → the mirror, never eligible.
    if a["lang"] == "env" and b["lang"] == "env" \
            and _dir_of(a["file"]) == _dir_of(b["file"]) \
            and (_is_env_template(a["file"]) or _is_env_template(b["file"])):
        return False
    if a["file"] == b["file"] and a["scope"] == b["scope"] \
            and a["name"] == b["name"]:
        return False
    # short names collide by accident, never by concept
    if len(a["name"]) < 3 or len(b["name"]) < 3:
        return False
    # same statement (tuple unpack: `a, b = fn()`) is one assignment, not two
    if a["file"] == b["file"] and a["scope"] == b["scope"] \
            and (a["line"] or 0) == (b["line"] or 0):
        return False
    # dogfood-tuned (2026-07-15, production repos): scoped
    # locals only pair inside the SAME scope (timeout/time_out twins within
    # one function); everything else must be top-level on both sides
    a_scoped, b_scoped = bool(a["scope"]), bool(b["scope"])
    if a_scoped and b_scoped:
        return a["file"] == b["file"] and a["scope"] == b["scope"]
    if a_scoped or b_scoped:
        return a["file"] == b["file"]
    return True


_TEST_PATH = re.compile(r"(^|/)(tests?|spec|__tests__|fixtures)(/|$)|"
                        r"(^|/)test_[^/]+$|_test\.[a-z]+$|\.spec\.[a-z]+$|"
                        r"(^|/)tests?\.[a-z0-9]+$")  # a file literally named test.*


def _is_test(r) -> bool:
    return bool(_TEST_PATH.search(r["file"]))


def _underscore_variant(a_name: str, b_name: str) -> bool:
    return a_name.lstrip("_") == b_name.lstrip("_") and a_name != b_name


def _parallel_sibling(a, b) -> bool:
    """Deliberately-parallel sibling names that differ by design, not drift:
    antonym twins (input/output), React hook setters (setX / changeX / the
    state var), sibling secret fields (…_api_key / …_admin_key), and
    module-scoped cache-namespace constants (…_cache_key / …_cache_ttl). Used
    only to DAMPER token-overlap/shared-token pairs a tier (see _score), never
    to exclude — one Settings/models/cache module emits C(N,2) token-overlap
    pairs from a single sibling shape. (dogfood 2026-07-16, ai-models-pricing)"""
    oa, ob = _ordered_tokens(a["name"]), _ordered_tokens(b["name"])
    if not oa or not ob:
        return False
    sa, sb = {_stem(t) for t in oa}, {_stem(t) for t in ob}
    only_a, only_b = sa - sb, sb - sa
    # namespaced-subject family: a shared leading prefix with a distinct final
    # token — one subject, different ROLES (KILO_PLUGIN_MARKER / _TEMPLATE,
    # HTTP_GET / HTTP_POST, MAX_RETRIES / MAX_TIMEOUT). Confidently distinct, so
    # damp. NOT the mirror shape — a shared SUFFIX with a differing first token
    # (ISSUE_PAGE_SIZE / USER_PAGE_SIZE) is "same attribute, different subject",
    # which may want aligning and is left to learn-from-dismissal instead.
    # (dogfood 2026-07-18, varmem: _KILO_PLUGIN_MARKER / _KILO_PLUGIN_TEMPLATE.)
    if len(oa) == len(ob) >= 2 \
            and all(_stem(oa[i]) == _stem(ob[i]) for i in range(len(oa) - 1)) \
            and _stem(oa[-1]) != _stem(ob[-1]):
        return True
    # antonym twin or format-variant twin: identical but for a single
    # antonym-paired (input/output) or format-paired (html/text) token
    if len(only_a) == 1 and len(only_b) == 1:
        twin = frozenset((next(iter(only_a)), next(iter(only_b))))
        if twin in _ANTONYMS or twin in _FORMAT_VARIANTS:
            return True
    # React hook setter/handler family (JS/TS only): setX / changeX / the state
    # var they set, sharing the remaining concept
    if a["lang"] in ("javascript", "typescript") \
            and b["lang"] in ("javascript", "typescript") \
            and (oa[0] in _HOOK_PREFIXES or ob[0] in _HOOK_PREFIXES) \
            and (sa & sb):
        return True
    # sibling secret fields: same credential-kind suffix (…_key / …_token),
    # differing only by provider/qualifier
    if oa[-1] in _CREDENTIAL_SUFFIX and ob[-1] in _CREDENTIAL_SUFFIX:
        return True
    # module-scoped cache-namespace constants (…_CACHE_KEY / …_CACHE_TTL)
    if "cache" in sa and "cache" in sb:
        return True
    return False


# Language conventions matter: what casing MEANS depends on the language.
# (user-directed 2026-07-15: "the logic needs to understand the language")
#   - python/js/ts/shell/c#: UPPER=const, CapWords=type, snake/camel=var
#   - go: leading capital = EXPORTED (visibility), not const-ness — an
#     exported/unexported case pair is usually a deliberate wrapper
#   - powershell: identifiers are CASE-INSENSITIVE — $ApiKey and $apikey are
#     THE SAME variable, so a case pair is same-name, never a "variant"
CASE_INSENSITIVE_LANGS = {"powershell"}


def _role(name: str, lang: str | None = None) -> str:
    base = name.lstrip("_$")
    letters = [c for c in base if c.isalpha()]
    if not letters:
        return "var"
    if all(c.isupper() for c in letters):
        return "const"
    if lang == "go":
        return "exported" if base[:1].isupper() else "var"
    if base[:1].isupper() and any(c.islower() for c in letters) \
            and "_" not in base:
        return "type"
    return "var"


def _derived(a, b) -> bool:
    """One side's RHS (or type annotation) references the other's name:
    alias/snapshot/typed-field, not a duplicate (service_name = _SERVICE_NAME;
    lower = UPPER exports; severity: Severity = ...)."""
    for x, y in ((a, b), (b, a)):
        pat = rf"(?<![\w.]){re.escape(y['name'])}(?![\w])"
        if not x["redacted"] and re.search(pat, x["value_preview"] or ""):
            return True
        if re.search(pat, x.get("anno") or ""):
            return True
    return False


_IDENT = re.compile(r"[A-Za-z_$][\w$]*")


def _constant_like(name: str) -> bool:
    # a meaningful shared reference — a CONSTANT/config identifier, not a
    # generic local: UPPER_SNAKE or camelCase (any uppercase) or has '_', >=3ch.
    return len(name) >= 3 and (name != name.lower() or "_" in name)


def _shared_reference(a, b, known_names: set[str]) -> str | None:
    """Both RHS values reference the SAME tracked constant, so the two are two
    views of one source of truth — `SEVERITIES = SEVERITY_ORDER` vs
    `SEVERITIES = new Set(SEVERITY_ORDER)` — not drifting copies. Returns that
    shared name, or None. Requires the reference to be a real tracked, constant-
    like identifier so generic locals (config, data, x) don't over-suppress."""
    if a["redacted"] or b["redacted"]:
        return None
    own = {a["name"], b["name"]}

    def refs(v):
        return {t for t in _IDENT.findall(v or "")
                if t in known_names and t not in own and _constant_like(t)}
    ra = refs(a["value_preview"])
    if not ra:
        return None
    shared = ra & refs(b["value_preview"])
    return sorted(shared)[0] if shared else None


def _score(a, b, ignore_norm: set[str], crossfile_ignore: set[str],
           known_names: set[str]) -> tuple[float, str] | None:
    na, nb = norm_name(a["name"]), norm_name(b["name"])
    if na in ignore_norm and nb in ignore_norm:
        return None
    # per-script convention names (ENVFILE, DSN, STATE, ...) legitimately
    # recur across standalone entry-point scripts (user-reviewed 2026-07-15)
    if a["file"] != b["file"] and na in crossfile_ignore \
            and nb in crossfile_ignore:
        return None
    # numeric-suffix siblings in one scope (res / res2, cap / cap2) are
    # sequential locals, not a duplicated concept
    if a["file"] == b["file"] and a["scope"] == b["scope"] \
            and a["name"] != b["name"] \
            and re.sub(r"\d+$", "", a["name"]) == re.sub(r"\d+$", "", b["name"]):
        return None
    # a drifted row's stored value no longer matches the file, so it cannot
    # anchor a "same value → one concept, two names" match (kills the stale
    # LEGACY_DIRNAME / VARMEM_DIRNAME collision once VARMEM_DIRNAME drifts).
    # Destructured bindings store the whole shared RHS (useState(x)), which
    # says nothing about the individual binding — same exclusion.
    def _destructured(r) -> bool:
        return str(r["kind"] or "").endswith("-destructure")
    same_hash = (a["value_hash"] == b["value_hash"]
                 and _nontrivial_value(a) and _nontrivial_value(b)
                 and a["status"] != "drifted" and b["status"] != "drifted"
                 and not _destructured(a) and not _destructured(b))
    score, reason = 0.0, ""

    if _derived(a, b):
        # dogfood-verified (2026-07-15): a RHS/annotation that references its
        # twin is an alias/snapshot/typed-field — a link, never a duplicate
        return (0.36, "alias-derived")

    # both sides derive from the SAME tracked constant (SEVERITIES computed two
    # ways from the shared SEVERITY_ORDER): a shared-source link, not drift —
    # even a same-name value-mismatch, since both are bound to one source of
    # truth. (dogfood 2026-07-18, ai-optimization: SEVERITIES = SEVERITY_ORDER
    # vs new Set(SEVERITY_ORDER) flagged value-mismatch after a correct dedup.)
    if _shared_reference(a, b, known_names) is not None:
        return (0.36, "shared-source")

    # identical env-var read on both sides: the env var is the single source of
    # truth, so this is a shared-source link, not a copy-paste that will drift.
    # Sink to low (visible only at --min-level low), same/different name alike.
    if same_hash and _ENV_READ.search(a["value_preview"] or ""):
        return (0.36, "shared-env-source")

    ps_pair = (a["lang"] in CASE_INSENSITIVE_LANGS
               and b["lang"] in CASE_INSENSITIVE_LANGS)
    same_name = a["name"] == b["name"] or (ps_pair and na == nb)

    if same_name or na == nb:
        if not same_name:
            ra, rb = _role(a["name"], a["lang"]), _role(b["name"], b["lang"])
            if ra != rb:
                if a["file"] == b["file"]:
                    # CONST->local / Type->field inside one file is the
                    # language's own convention (user-reviewed: never real)
                    return None
                score, reason = 0.6, "case-variant"
            else:
                score, reason = 0.9, "name-variant"
        elif a["value_hash"] == b["value_hash"]:
            if same_hash:  # non-trivial identical value: copy-paste
                score, reason = 0.85, "same-name-same-value"
                if _IDIOM_RHS.search(a["value_preview"] or ""):
                    score -= 0.3  # repeated boilerplate, not drift
            else:  # trivial identical value: weak echo
                score, reason = 0.4, "same-name"
        else:
            # same name, DIFFERENT values — the alignment hazard
            # (user-reviewed: strongest real signal). Loud within a
            # subsystem (same dir); cross-subsystem same-name is often
            # coincidence in script-heavy repos, so medium there.
            reason = "value-mismatch"
            if a["file"] != b["file"] \
                    and _dir_of(a["file"]) == _dir_of(b["file"]):
                score = 0.78
            elif a["file"] != b["file"]:
                score = 0.6
            else:
                score = 0.65
        if na in ignore_norm or nb in ignore_norm:
            score -= 0.35
    else:
        ta, tb = name_tokens(a["name"]), name_tokens(b["name"])
        if not ta or not tb:
            return None
        shared = ta & tb
        union = ta | tb
        jac = len(shared) / len(union) if union else 0.0
        if same_hash and jac > 0:
            score, reason = 0.8, "same-value-related-name"
        elif same_hash:
            score, reason = 0.6, "same-value"
        elif not (shared - GENERIC_TOKENS):
            # the only shared tokens are generic scaffolding (data, uri, dir,
            # dirname, id, …) — coincidental structure, not a shared concept
            return None
        elif jac >= 0.5 and min(len(ta), len(tb)) >= 2:
            # a single generic token (tool, key, files, status) sitting inside
            # a compound name is coincidence, not a shared concept — require
            # at least two tokens on BOTH sides for the medium tier
            score, reason = 0.55, "token-overlap"
        elif jac > 0 and any(len(t) >= 4 for t in shared) \
                and len(ta) <= 3 and len(tb) <= 3:
            score, reason = 0.4, "shared-token"
        else:
            return None

    if a["lang"] == b["lang"]:
        score += 0.03
    if _dir_of(a["file"]) == _dir_of(b["file"]):
        score += 0.02
    # dogfood-tuned dampers: privacy-underscore idiom and test-file clones
    # are usually intentional, so they sink a level instead of leading
    if _underscore_variant(a["name"], b["name"]) and a["file"] == b["file"]:
        score -= 0.3
    if _is_test(a) and _is_test(b):
        score -= 0.25
    elif _is_test(a) != _is_test(b):
        # a test re-deriving an implementation value (fixtures, keygen mirrors)
        # is expected, not drift — sink it well below the high tier
        score -= 0.3
    # deliberately-parallel sibling families (antonym twins, React hook setters,
    # sibling secret fields, cache-namespace constants) only overlap by name
    # scaffolding — sink a tier (medium->low) rather than exclude. Gated to the
    # SAME file: a family is declared together in one module, so a cross-file
    # version of the same shape (a rotated secret STRIPE_API_KEY/STRIPE_TOKEN
    # diverging across files, cross-service cache drift) is never damped and
    # stays visible; a same-file one still shows at --min-level low.
    if reason in ("token-overlap", "shared-token") \
            and a["file"] == b["file"] and _parallel_sibling(a, b):
        score -= 0.2
    return (min(score, 0.99), reason)


# RHS shapes that legitimately repeat in every module
_IDIOM_RHS = re.compile(
    r"getLogger|ArgumentParser|__name__|__file__|__dirname|require\(|"
    r"Flask\(|express\(|Router\(|APIRouter\(|Path\(__|os\.environ|"
    r"process\.env|argparse\.|new\s+\w+\(\)$"
)

# an env-var read (os.environ/os.getenv, process.env, $env:, shell ${VAR:-def})
# is bound to a SHARED source: the env var. An identical read under one name
# across standalone entry-point scripts is the same config by construction, and
# two names reading the same key name one concept on purpose. Either way it is a
# shared-source link, not drift (dogfood 2026-07-17: per-module
# AUDIT_LOG/KEYS_DIR/SERVICE_* accessors; standalone-unit constants).
_ENV_READ = re.compile(
    r"os\.environ|os\.getenv|process\.env|\$env:|\$\{\w+:[-=?]|"
    r"(?<![\w.])getenv\s*\(")


def _level(score: float) -> str | None:
    for name, threshold in LEVELS:
        if score >= threshold:
            return name
    return None


def find_duplicates(st, *, include_dismissed: bool = False,
                    ignore_names: set[str] | None = None,
                    max_bucket: int = 200) -> list[dict]:
    from . import config as _config
    cfg = _config.load(st.root)
    ignore = DEFAULT_IGNORE | (ignore_names or set()) \
        | set(cfg.get("dup_ignore_names") or [])
    ignore_norm = {norm_name(n) for n in ignore}
    crossfile_ignore = {norm_name(n) for n in
                        cfg.get("dup_ignore_crossfile_names") or []}
    # a duplicate is about assignments that COEXIST now: skip file_deleted and
    # missing (the assignment is gone from the file — a stale rename artifact,
    # not a live collision)
    rows = st.all_rows(exclude_status={"file_deleted", "missing"})
    # every tracked name, so _score can tell when two RHS values both reference
    # the same shared constant (a source-of-truth link, not drift).
    known_names = {r["name"] for r in rows if r.get("name")}

    # map each row to its declared standalone unit (None when the feature is off
    # or the file is outside every unit) so cross-unit pairs can be suppressed.
    # A bare string in config is treated as one pattern, never iterated into
    # single-char patterns (a stray "*" char would suppress everything).
    raw_units = cfg.get("standalone_units") or []
    if isinstance(raw_units, str):
        raw_units = [raw_units]
    unit_patterns = [str(p) for p in raw_units if p]
    # with no explicit config, auto-detect a multi-component (independently
    # deployable) layout so cross-runtime constant copies stop showing on their
    # own; set "auto_standalone_units": false to opt out.
    if not unit_patterns and cfg.get("auto_standalone_units", True):
        unit_patterns = _auto_unit_patterns(
            st.root, set(cfg.get("exclude_dirs") or []))
    unit_keys = ({r["id"]: _unit_key(r["file"], unit_patterns) for r in rows}
                 if unit_patterns else None)

    buckets: dict[str, list] = defaultdict(list)
    for r in rows:
        # member/attribute assignment targets (obj.attr = …, self.x = …)
        # configure an object; they are not named concepts, so they never
        # take part in duplicate detection (kills statusBar / statusBar.text
        # and its siblings — dogfood 2026-07-16)
        if "." in r["name"] or _is_dunder(r["name"]):
            continue
        buckets["n:" + norm_name(r["name"])].append(r)
        # destructured bindings store the WHOLE right-hand side (the tuple/
        # object source, e.g. useState(null)) — that shared value says nothing
        # about the individual binding, so it never anchors same-value pairing
        if _nontrivial_value(r) and not str(r["kind"] or "").endswith("-destructure"):
            buckets["h:" + (r["value_hash"] or "")].append(r)
        for t in name_tokens(r["name"]):
            if len(t) >= 3:
                buckets["t:" + t].append(r)

    reviews = st.reviews
    learn = bool(cfg.get("learn_from_reviews", True))
    seen: set[str] = set()
    scored: list[dict] = []
    for members in buckets.values():
        if len(members) < 2 or len(members) > max_bucket:
            continue  # oversized buckets are stop-tokens, not signals
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                a, b = members[i], members[j]
                if not _eligible_pair(a, b, unit_keys):
                    continue
                key = _pair_key(a, b)
                if key in seen:
                    continue
                seen.add(key)
                res = _score(a, b, ignore_norm, crossfile_ignore, known_names)
                if res is None:
                    continue
                score, reason = res
                level = _level(score)
                if level is None:
                    continue
                scored.append({
                    "a": a, "b": b, "key": key, "score": score,
                    "level": level, "reason": reason,
                    "review": reviews.get(key),
                    "shared": frozenset(name_tokens(a["name"])
                                        & name_tokens(b["name"])),
                })

    # Learn family signatures from explicit dismissals: a `not_duplicate`
    # verdict on a token-based pair generalizes to every pair sharing the same
    # token set — so dismissing one member of a naming family (ISSUES_PAGE_SIZE
    # / VARS_PAGE_SIZE) quiets the rest, AND future members, without the
    # detector guessing which families are intentional. Scoped to the
    # token-overlap/shared-token reasons and to signatures of >=2 tokens, so a
    # single generic token can never learn an over-broad rule.
    learned: set[frozenset] = set()
    learned_names: set[str] = set()
    if learn:
        for p in scored:
            rv = p["review"]
            if not rv or rv.get("verdict") != "not_duplicate":
                continue
            if p["reason"] in _FAMILY_REASONS and len(p["shared"]) >= 2:
                learned.add(p["shared"])
            elif p["reason"] in _SAMENAME_REASONS \
                    and norm_name(p["a"]["name"]) == norm_name(p["b"]["name"]):
                learned_names.add(norm_name(p["a"]["name"]))

    out: list[dict] = []
    for p in scored:
        review = dict(p["review"]) if p["review"] else None
        if review is None and learn and p["reason"] in _FAMILY_REASONS \
                and len(p["shared"]) >= 2 and p["shared"] in learned:
            review = {"verdict": "not_duplicate", "learned": True,
                      "note": "auto-quieted: matches a family you dismissed ("
                              + "+".join(sorted(p["shared"])) + ")"}
        if review is None and learn and p["reason"] in _SAMENAME_REASONS \
                and norm_name(p["a"]["name"]) == norm_name(p["b"]["name"]) \
                and norm_name(p["a"]["name"]) in learned_names:
            review = {"verdict": "not_duplicate", "learned": True,
                      "note": "auto-quieted: you dismissed another "
                              + p["a"]["name"] + " pair — this name repeats "
                              + "legitimately"}
        if review and review["verdict"] == "not_duplicate" \
                and not include_dismissed:
            continue
        out.append({
            "pair_key": p["key"],
            "score": round(p["score"], 2),
            "level": p["level"],
            "reason": p["reason"],
            "review": review,
            "a": _side(p["a"]),
            "b": _side(p["b"]),
        })
    out.sort(key=lambda d: -d["score"])
    return out


def _side(r) -> dict:
    return {
        "id": r["id"], "name": r["name"], "file": r["file"],
        "scope": r["scope"], "line": r["repo_line"] or r["line"],
        "value": r["value_preview"], "status": r["status"],
        "origin": r["origin"], "lang": r["lang"],
        "last_session": (r["last_session"] or "")[:8],
        "note": r["note"],
        # display-only projections for the live UI (drift evidence);
        # scoring never reads these
        "repo_value": r["repo_value_preview"],
        "redacted": bool(r["redacted"]),
    }
