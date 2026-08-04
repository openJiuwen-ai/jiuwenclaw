"""DoloresAgent fork builder v2 (fixed relative-import resolver + clean rebuild).
PKG_ROOT = A tree (newer jiuwenswarm, now matching openjiuwen 0.1.16).
Clean-rebuilds extensions/dolores/ from the pruned closure of interface_deep.py.
"""
import ast, os, re, shutil
from pathlib import Path

PKG_ROOT = Path(r"D:\jiuwenAgent\dolores\jiuwenswarm\jiuwenswarm")
PKG_NAME = "jiuwenswarm"
DOL = PKG_ROOT / "extensions" / "dolores"
START = "jiuwenswarm.server.runtime.agent_adapter.interface_deep"

PRUNE = (
    "jiuwenswarm.gateway", "jiuwenswarm.instance_manager", "jiuwenswarm.symphony",
    "jiuwenswarm.telemetry", "jiuwenswarm.acp", "jiuwenswarm.server.agent_ws_server",
    "jiuwenswarm.server.sandbox", "jiuwenswarm.server.gateway_push", "jiuwenswarm.server.hooks",
    "jiuwenswarm.server.ws_send", "jiuwenswarm.dotenv_early",
)

def is_pruned(m):
    return any(m == p or m.startswith(p + ".") for p in PRUNE)

def module_to_path(mod):
    if not mod.startswith(PKG_NAME + "."):
        return None
    rel = mod[len(PKG_NAME)+1:].replace(".", "/")
    for cand in (PKG_ROOT / (rel + ".py"), PKG_ROOT / rel):
        if cand.is_file():
            return cand
    pkgdir = PKG_ROOT / rel
    if pkgdir.is_dir() and (pkgdir / "__init__.py").is_file():
        return pkgdir / "__init__.py"
    return None

def file_dotted(p):
    rel = p.relative_to(PKG_ROOT).with_suffix("")
    parts = rel.parts
    is_init = bool(parts) and parts[-1] == "__init__"
    if is_init:
        return PKG_NAME + "." + ".".join(parts[:-1]), True
    return PKG_NAME + "." + ".".join(parts), False

def abs_imports(p):
    s = set()
    try:
        t = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return s
    for n in ast.walk(t):
        if isinstance(n, ast.Import):
            for x in n.names:
                s.add(x.name)
        elif isinstance(n, ast.ImportFrom):
            if n.level == 0 and n.module:
                s.add(n.module)
    return s

def rel_imports(p):
    """Resolve relative imports to ABSOLUTE jiuwenswarm.* names (FIXED)."""
    s = set()
    try:
        t = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return s
    mod_dotted, is_init = file_dotted(p)
    cur_pkg = mod_dotted if is_init else mod_dotted.rsplit(".", 1)[0]
    for n in ast.walk(t):
        if isinstance(n, ast.ImportFrom) and n.level:
            parts = cur_pkg.split(".")
            climb = n.level - 1
            if climb > len(parts):
                continue
            base_parts = parts[:len(parts)-climb] if climb else parts
            base = ".".join(base_parts)
            if not base.startswith(PKG_NAME):
                continue
            target = base + "." + n.module if n.module else base
            s.add(target)
            for x in n.names:
                s.add(target + "." + x.name)
    return s

# --- closure ---
visited = {}
queue = [START]
while queue:
    m = queue.pop()
    if m in visited:
        continue
    p = module_to_path(m)
    if p is None:
        visited[m] = None
        continue
    visited[m] = str(p)
    for d in abs_imports(p) | rel_imports(p):
        if d.startswith(PKG_NAME + ".") and not is_pruned(d):
            queue.append(d)

local_names = {m for m, p in visited.items() if p}
local_prefixes = set()
for m in local_names:
    parts = m.split(".")
    for i in range(2, len(parts)):
        local_prefixes.add(".".join(parts[:i]))

def is_local(modname):
    if modname in local_names or modname in local_prefixes:
        return True
    return any(ln.startswith(modname + ".") for ln in local_names)

TOKEN = re.compile(r"jiuwenswarm(?:\.\w+)+")

def rewrite(text):
    out = []
    for line in text.splitlines(keepends=True):
        s = line.lstrip()
        if (s.startswith("import ") or s.startswith("from jiuwenswarm")) and not s.startswith("from ."):
            line = TOKEN.sub(lambda mo: ("jiuwenswarm.extensions.dolores" + mo.group(0)[len("jiuwenswarm"):]) if is_local(mo.group(0)) else mo.group(0), line)
        out.append(line)
    return "".join(out)

# --- clean rebuild ---
if DOL.exists():
    shutil.rmtree(DOL)
DOL.mkdir(parents=True, exist_ok=True)

copied = 0
for m, p in visited.items():
    if not p:
        continue
    src = Path(p)
    rel = src.relative_to(PKG_ROOT)
    dst = DOL / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(rewrite(src.read_text(encoding="utf-8", errors="replace")), encoding="utf-8")
    copied += 1

# ensure __init__.py everywhere under DOL
created = 0
for d in sorted({dd for f in DOL.rglob("*.py") for dd in [f.parent]}):
    ini = d / "__init__.py"
    if not ini.exists():
        orig = PKG_ROOT / d.relative_to(DOL) / "__init__.py"
        if orig.is_file():
            ini.write_text(rewrite(orig.read_text(encoding="utf-8", errors="replace")), encoding="utf-8")
        else:
            ini.write_text("", encoding="utf-8")
        created += 1

print(f"copied={copied} created_init={created} local_names={len(local_names)} DOL={DOL}")
