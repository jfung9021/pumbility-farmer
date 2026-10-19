"""Producer recipes: mathematical source symbols and numerical runtime only."""
from __future__ import annotations

import ast
import hashlib
import sys
from importlib import metadata
from functools import lru_cache
from pathlib import Path

from canonical_json import canonical_json_sha256 as digest

ROOT = Path(__file__).resolve().parent
F395_SOURCES = {
    "sss_scoring_model.py": "004b6941a1ee40378c50f8bf6475342edc1987e1e4709da10aa0c31b1c273ded",
    "official_player_scoring.py": "3943aa8c73ed6500bb2d9221ab3844ad742a44e2a06497686661d83bf875d2d4",
    "scoring_targets.py": "38b91f443d35693e9ff32334c89dcc6509d3ad4500e3dd8f5c2305c4568db673",
    "target_fit_cache.py": "a46481986deec5c6df93376ea8b8df021830e45aef8b1471db1e704102e9ba29",
}


def source_recipe(filename, symbols=None):
    return _ast_recipe((ROOT / filename).read_bytes(), symbols)


def _ast_recipe(source, symbols=None):
    tree = ast.parse(source)
    selected = []
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue
        names = {node.name} if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else {
            item.id for item in ast.walk(node) if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Store)}
        if symbols is not None and not names.intersection(symbols):
            continue
        for item in ast.walk(node):
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and item.body:
                first = item.body[0]
                if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                    item.body = item.body[1:]
        selected.append(ast.dump(node, include_attributes=False))
    return digest(selected)


def numerical_runtime(*libraries):
    return {"python": list(sys.version_info[:3]),
            "libraries": {name: metadata.version(name) for name in libraries}}


def target_kernel_recipe():
    return {"recipe": "target-observed-best-v1", "source": compatible_target_source(),
            **numerical_runtime("numpy", "scipy")}


def benchmark_recipe():
    return {"recipe": "training-only-General-v1", "source": source_recipe("official_player_scoring.py"),
            **numerical_runtime("numpy")}


def producer_dependencies(roots, *, stage=None):
    """Fingerprint reachable producer symbols, rather than reader/transport files.

    The hosted General call never supplies source_aware_store; its redirect back
    to the orchestrator is therefore excluded. Instrumentation has no values in
    the returned mathematical payload. Imported local symbols are followed even
    when the import is inside a producer function.
    """
    ignored = {("production_tiers", "build_hosted_tiers"), ("tier_diagnostics", "event"),
               ("tier_diagnostics", "phase")}
    parsed, selected, imports_used = {}, {}, {}

    def module_tree(filename):
        if filename not in parsed:
            tree = ast.parse((ROOT / filename).read_bytes())
            definitions, imports = {}, {}
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    definitions[node.name] = node
                elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                    for item in ast.walk(node):
                        if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Store):
                            definitions[item.id] = node
                elif isinstance(node, (ast.Import, ast.ImportFrom)):
                    for alias in node.names:
                        imports[alias.asname or alias.name.split(".")[0]] = (node, alias)
            parsed[filename] = definitions, imports
        return parsed[filename]

    queue = [(filename, symbol) for filename, symbols in roots.items() for symbol in symbols]
    while queue:
        filename, symbol = queue.pop()
        if symbol in selected.setdefault(filename, {}):
            continue
        definitions, top_imports = module_tree(filename)
        if symbol not in definitions:
            if symbol not in top_imports:
                raise ValueError(f"Unknown numerical producer symbol: {filename}:{symbol}")
            statement, alias = top_imports[symbol]
            if not isinstance(statement, ast.ImportFrom) or statement.level:
                raise ValueError(f"Dynamic numerical producer export: {filename}:{symbol}")
            selected[filename][symbol] = ast.dump(statement, include_attributes=False)
            local = statement.module.replace(".", "/") + ".py"
            if (ROOT / local).is_file():
                queue.append((local, alias.name))
            continue
        node = ast.parse(ast.unparse(definitions[symbol])).body[0]
        if filename == "production_tiers.py" and symbol == "_finish_stage" and stage is not None:
            node.body = [part for part in node.body if not (
                isinstance(part, ast.If) and isinstance(part.test, ast.Compare)
                and isinstance(part.test.left, ast.Name) and part.test.left.id == "stage"
                and len(part.test.comparators) == 1
                and isinstance(part.test.comparators[0], ast.Constant)
                and part.test.comparators[0].value != stage)]
        # Ignore docstrings, matching source_recipe's formatting-independent AST.
        for part in ast.walk(node):
            if isinstance(part, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and part.body:
                first = part.body[0]
                if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                    part.body = part.body[1:]
        selected[filename][symbol] = ast.dump(node, include_attributes=False)
        imports = dict(top_imports)
        for part in ast.walk(node):
            if isinstance(part, (ast.Import, ast.ImportFrom)):
                for alias in part.names:
                    imports[alias.asname or alias.name.split(".")[0]] = (part, alias)
        loads = {part.id for part in ast.walk(node) if isinstance(part, ast.Name) and isinstance(part.ctx, ast.Load)}
        for name in loads:
            if name in definitions:
                queue.append((filename, name))
            if name not in imports:
                continue
            statement, alias = imports[name]
            module = statement.module if isinstance(statement, ast.ImportFrom) else alias.name
            imported = alias.name if isinstance(statement, ast.ImportFrom) else None
            if (module, imported) in ignored:
                continue
            imports_used.setdefault(filename, set()).add(ast.dump(statement, include_attributes=False))
            local = module.replace(".", "/") + ".py"
            if not (ROOT / local).is_file():
                continue
            if imported is not None:
                queue.append((local, imported))
            else:
                attributes = {part.attr for part in ast.walk(node) if isinstance(part, ast.Attribute)
                              and isinstance(part.value, ast.Name) and part.value.id == name}
                if not attributes:
                    raise ValueError(f"Dynamic local numerical module dependency: {module}")
                queue.extend((local, attr) for attr in attributes)
    return {filename: {"symbols": dict(sorted(symbols.items())),
                       "imports": sorted(imports_used.get(filename, ()))}
            for filename, symbols in sorted(selected.items())}


@lru_cache(maxsize=1)
def general_producer_recipe():
    return {"version": 1, "dependencies": digest(producer_dependencies({
        "scoring_percentile.py": {"build_production_tier_payload"}})),
        **numerical_runtime("numpy", "scipy", "pandas")}


@lru_cache(maxsize=8)
def finish_producer_recipe(stage):
    return {"version": 1, "stage": stage, "dependencies": digest(producer_dependencies({
        "production_tiers.py": {"_finish_stage", "SAMPLES", "SEED", "MODES"}}, stage=stage)),
        **numerical_runtime("numpy", "scipy", "pandas")}


def legacy_outer_identity():
    """Only the reviewed f395 numerical producer can adopt its old byte keys."""
    if (compatible_target_source() != F395_TARGET_RECIPE
            or source_recipe("official_player_scoring.py") != F395_BENCHMARK_RECIPE):
        return None
    return digest({"sources": F395_SOURCES, **numerical_runtime("numpy", "scipy", "pandas")})


def compatible_target_source():
    current = source_recipe("sss_scoring_model.py")
    # The sole alias below preserves every mathematical operation, draw, CV and
    # initial vector. Interrupted/full-200 parity is verified against f395.
    return F395_TARGET_RECIPE if current in {F395_TARGET_RECIPE, BATCH_TARGET_RECIPE} else current


def legacy_component_code():
    if legacy_outer_identity() is None:
        return None
    return digest({"model": F395_SOURCES["sss_scoring_model.py"],
        "cache": F395_SOURCES["target_fit_cache.py"],
        "numpy": metadata.version("numpy"), "scipy": metadata.version("scipy")})


# Filled with the reviewed f395 AST recipes; registry/storage changes cannot
# authorize adopting a different mathematical producer.
F395_TARGET_RECIPE = "422e031a89528f5d0260113a85003f8193ea1c6e5f3d112262f54ab20c0f7f5d"
F395_BENCHMARK_RECIPE = "1f18807c301fc4b0abab9c19126119e6fc2ab692400c586086d24f835d1a5198"
BATCH_TARGET_RECIPE = "2982f8d9bbef904d0c7aa9e7c2d8d34b459890f8162c043c2be4d506fee97bf8"
