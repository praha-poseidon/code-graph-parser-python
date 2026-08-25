from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

try:
    from static_extract_python import parse_python_files, run_static_extract
    from static_extract_python.model import ExtractedFact, ParsedPythonFile
except ModuleNotFoundError:
    workspace = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(workspace / "static-extract-python"))
    from static_extract_python import parse_python_files, run_static_extract
    from static_extract_python.model import ExtractedFact, ParsedPythonFile

from .ids import endpoint_id, function_id, package_id, placeholder_function_id, relationship_id, unit_id


@dataclass
class ImportTable:
    symbols: Dict[str, str] = field(default_factory=dict)


@dataclass
class ClassInfo:
    qualified_name: str
    module: str
    file: ParsedPythonFile
    node: ast.ClassDef
    imports: ImportTable
    bases: List[str]
    properties: Dict[str, str] = field(default_factory=dict)
    unit_type: str = "class"


@dataclass
class FunctionInfo:
    qualified_name: str
    module: str
    file: ParsedPythonFile
    node: ast.AST
    imports: ImportTable
    class_name: Optional[str] = None
    owner_unit_id: str = ""
    return_type: str = ""


class PythonCodeGraphParser:
    def parse(self, request: Dict[str, object]) -> Dict[str, object]:
        project_root = Path(str(request.get("projectRoot") or "")).resolve()
        if not project_root.is_dir():
            raise ValueError("ParseRequest.projectRoot must be a directory")
        project_name = str(request.get("projectName") or project_root.name)
        source_roots = request.get("sourceRoots") if isinstance(request.get("sourceRoots"), list) else []
        load_files, diagnostics = parse_python_files(
            str(project_root), source_roots=[str(item) for item in source_roots]
        )
        requested = _requested_files(project_root, request.get("sourceFiles"))
        scan_paths = requested or {item.project_file_path for item in load_files}
        scan_files = [item for item in load_files if item.project_file_path in scan_paths]

        state = _State(request, project_name, project_root, load_files, scan_paths, diagnostics)
        state.collect_definitions()
        state.emit_nodes(scan_files)
        state.emit_relations(scan_files)
        state.emit_endpoints(scan_files)
        return state.delta()


class _State:
    def __init__(
        self,
        request: Dict[str, object],
        project_name: str,
        project_root: Path,
        load_files: Sequence[ParsedPythonFile],
        scan_paths: Set[str],
        diagnostics: Sequence[Dict[str, object]],
    ) -> None:
        self.request = request
        self.project_name = project_name
        self.project_root = project_root
        self.load_files = list(load_files)
        self.files_by_path = {item.project_file_path: item for item in load_files}
        self.scan_paths = scan_paths
        self.diagnostics = list(diagnostics)
        self.imports: Dict[str, ImportTable] = {}
        self.classes: Dict[str, ClassInfo] = {}
        self.functions: Dict[str, FunctionInfo] = {}
        self.functions_by_simple: Dict[str, List[str]] = {}
        self.units: Dict[str, Dict[str, object]] = {}
        self.packages: Dict[str, Dict[str, object]] = {}
        self.output_functions: Dict[str, Dict[str, object]] = {}
        self.relationships: Dict[str, Dict[str, object]] = {}
        self.endpoints: Dict[str, Dict[str, object]] = {}
        self.module_init: Dict[str, str] = {}

    def collect_definitions(self) -> None:
        for file in self.load_files:
            imports = _imports(file)
            self.imports[file.module_name] = imports
            for node in file.tree.body:
                if isinstance(node, ast.ClassDef):
                    self._collect_class(file, imports, node, [])
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self._collect_function(file, imports, node, None, [])
            for name, node in _assigned_lambdas(file.tree.body):
                self._collect_lambda(file, imports, node, name, None, [])
        for class_info in self.classes.values():
            class_info.bases = [_resolve_base_type(base, class_info.module, class_info.imports) for base in class_info.bases]
            class_info.properties.update(_class_properties(class_info))

    def _collect_class(
        self, file: ParsedPythonFile, imports: ImportTable, node: ast.ClassDef, owners: List[str]
    ) -> None:
        qualified = ".".join([file.module_name] + owners + [node.name])
        raw_bases = [_annotation_name(base) for base in node.bases if _annotation_name(base)]
        resolved_bases = {_resolve_base_type(name, file.module_name, imports) for name in raw_bases}
        unit_type = "interface" if "typing.Protocol" in resolved_bases or "typing_extensions.Protocol" in resolved_bases else "class"
        info = ClassInfo(qualified, file.module_name, file, node, imports, raw_bases, unit_type=unit_type)
        self.classes[qualified] = info
        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._collect_function(file, imports, child, qualified, owners + [node.name])
            elif isinstance(child, ast.ClassDef):
                self._collect_class(file, imports, child, owners + [node.name])
        for name, child in _assigned_lambdas(node.body):
            self._collect_lambda(file, imports, child, name, qualified, owners + [node.name])

    def _collect_function(
        self,
        file: ParsedPythonFile,
        imports: ImportTable,
        node: ast.AST,
        class_name: Optional[str],
        owners: List[str],
    ) -> None:
        name = node.name
        qualified = "%s::%s" % (class_name, name) if class_name else ".".join([file.module_name] + owners + [name])
        return_type = _resolve_type(_annotation_name(node.returns), file.module_name, imports)
        owner_id = unit_id(class_name or file.module_name)
        info = FunctionInfo(qualified, file.module_name, file, node, imports, class_name, owner_id, return_type)
        self.functions[qualified] = info
        self.functions_by_simple.setdefault(name, []).append(qualified)
        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                nested_owners = owners + [name, "<locals>"]
                self._collect_function(file, imports, child, None, nested_owners)

    def _collect_lambda(
        self,
        file: ParsedPythonFile,
        imports: ImportTable,
        node: ast.Lambda,
        name: str,
        class_name: Optional[str],
        owners: List[str],
    ) -> None:
        parts = name.split(".")
        callable_name = parts[-1]
        logical_owners = owners + parts[:-1]
        prefix = ".".join([file.module_name] + logical_owners)
        qualified = "%s::%s" % (prefix, callable_name) if logical_owners else "%s.%s" % (file.module_name, callable_name)
        owner_id = unit_id(class_name or file.module_name)
        info = FunctionInfo(qualified, file.module_name, file, node, imports, class_name, owner_id)
        self.functions[qualified] = info
        self.functions_by_simple.setdefault(callable_name, []).append(qualified)

    def emit_nodes(self, scan_files: Sequence[ParsedPythonFile]) -> None:
        for file in scan_files:
            package_name = _package_name(file.module_name, file.project_file_path)
            self._put_package(package_name, file)
            module_unit = _node_base(
                self, unit_id(file.module_name), file.module_name.split(".")[-1], file.module_name, file
            )
            module_unit.update(
                {
                    "unitType": "module",
                    "modifiers": ["python-module"],
                    "isAbstract": False,
                    "packageId": package_id(package_name),
                    "startLine": 1,
                    "endLine": max(1, len(file.source.splitlines())),
                }
            )
            self.units[module_unit["id"]] = module_unit
            self._relationship(package_id(package_name), "PACKAGE_TO_UNIT", module_unit["id"], 1)

            init_name = "%s.<module-init>" % file.module_name
            init_node = _function_node(
                self, file, init_name, "<module-init>", "<module-init>()", [], None, False, False
            )
            self.output_functions[init_node["id"]] = init_node
            self.module_init[file.module_name] = init_node["id"]
            self._relationship(module_unit["id"], "UNIT_TO_FUNCTION", init_node["id"], 1)

        for class_info in self.classes.values():
            if class_info.file.project_file_path not in self.scan_paths:
                continue
            package_name = _package_name(class_info.module, class_info.file.project_file_path)
            self._put_package(package_name, class_info.file)
            modifiers = ["abstract"] if _is_abstract_class(class_info.node) else []
            unit = _node_base(
                self,
                unit_id(class_info.qualified_name),
                class_info.node.name,
                class_info.qualified_name,
                class_info.file,
                class_info.node,
            )
            unit.update(
                {
                    "unitType": class_info.unit_type,
                    "modifiers": modifiers,
                    "isAbstract": "abstract" in modifiers or class_info.unit_type == "interface",
                    "packageId": package_id(package_name),
                }
            )
            self.units[unit["id"]] = unit
            self._relationship(
                package_id(package_name), "PACKAGE_TO_UNIT", unit["id"], getattr(class_info.node, "lineno", 1)
            )

        for info in self.functions.values():
            if info.file.project_file_path not in self.scan_paths:
                continue
            display_name = getattr(info.node, "name", _simple_function_name(info.qualified_name))
            modifiers = _function_modifiers(info.node, display_name)
            signature = _signature(info.node, display_name)
            node = _function_node(
                self,
                info.file,
                info.qualified_name,
                display_name,
                signature,
                modifiers,
                info.return_type or None,
                isinstance(info.node, ast.AsyncFunctionDef),
                display_name == "__init__",
                info.node,
            )
            self.output_functions[node["id"]] = node
            self._relationship(info.owner_unit_id, "UNIT_TO_FUNCTION", node["id"], getattr(info.node, "lineno", 1))

    def _put_package(self, name: str, file: ParsedPythonFile) -> None:
        identity = package_id(name)
        if identity in self.packages:
            return
        node = _node_base(self, identity, name.split(".")[-1], name, file)
        node.update({"packagePath": name.replace(".", "/"), "startLine": 1, "endLine": 1})
        self.packages[identity] = node

    def emit_relations(self, scan_files: Sequence[ParsedPythonFile]) -> None:
        for class_info in self.classes.values():
            if class_info.file.project_file_path not in self.scan_paths:
                continue
            for base in class_info.bases:
                target = self.classes.get(base)
                if not target:
                    continue
                rel_type = (
                    "IMPLEMENTS"
                    if target.unit_type == "interface" and class_info.unit_type != "interface"
                    else "EXTENDS"
                )
                self._relationship(
                    unit_id(class_info.qualified_name), rel_type, unit_id(target.qualified_name), class_info.node.lineno
                )
            for child in class_info.node.body:
                if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                method = self.functions.get("%s::%s" % (class_info.qualified_name, child.name))
                if not method:
                    continue
                overridden = self._method_in_bases(class_info, child.name)
                if overridden:
                    self._relationship(
                        function_id(method.qualified_name + "()"),
                        "OVERRIDES",
                        function_id(overridden.qualified_name + "()"),
                        child.lineno,
                    )

        for file in scan_files:
            imports = self.imports[file.module_name]
            init_id = self.module_init.get(file.module_name)
            if init_id:
                for node in _module_executable_nodes(file.tree):
                    self._emit_calls(file, imports, init_id, None, node)
        for info in self.functions.values():
            if info.file.project_file_path not in self.scan_paths:
                continue
            caller = function_id(info.qualified_name + "()")
            self._emit_calls(info.file, info.imports, caller, info, info.node)

    def _emit_calls(
        self,
        file: ParsedPythonFile,
        imports: ImportTable,
        caller: str,
        function: Optional[FunctionInfo],
        root: ast.AST,
    ) -> None:
        types = _local_types(function, self)
        for call in _calls_without_nested_definitions(root):
            target_name, call_type = self._resolve_call(call, function, imports, types, file.module_name)
            if not target_name:
                continue
            target_info = self.functions.get(target_name)
            if target_info:
                target_id = function_id(target_info.qualified_name + "()")
            else:
                class_info = self.classes.get(target_name)
                constructor = self.functions.get("%s::__init__" % target_name) if class_info else None
                if constructor:
                    target_id = function_id(constructor.qualified_name + "()")
                    target_name = constructor.qualified_name
                else:
                    target_id = self._put_placeholder_function(
                        file,
                        target_name,
                        ["dynamic-receiver" if target_name.startswith("<dynamic>") else "statically-named-target"],
                    )
            self._relationship(caller, "CALLS", target_id, getattr(call, "lineno", None), call_type)

    def _put_placeholder_function(
        self,
        file: ParsedPythonFile,
        qualified_name: str,
        extra_modifiers: Sequence[str],
    ) -> str:
        clean_name = qualified_name[:-2] if qualified_name.endswith("()") else qualified_name
        identity = placeholder_function_id(clean_name + "()")
        if identity not in self.output_functions:
            node = _function_node(
                self,
                file,
                clean_name,
                _simple_function_name(clean_name),
                "%s()" % _simple_function_name(clean_name),
                ["placeholder", "unresolved"] + list(extra_modifiers),
                None,
                False,
                False,
            )
            node["id"] = identity
            node["isPlaceholder"] = True
            self.output_functions[identity] = node
        return identity

    def _resolve_call(
        self,
        call: ast.Call,
        function: Optional[FunctionInfo],
        imports: ImportTable,
        types: Dict[str, str],
        module: str,
    ) -> Tuple[str, str]:
        if isinstance(call.func, ast.Name):
            raw = call.func.id
            resolved = imports.symbols.get(raw, "%s.%s" % (module, raw))
            if resolved in self.functions or resolved in self.classes:
                return resolved, "direct"
            candidates = self.functions_by_simple.get(raw, [])
            local = [name for name in candidates if name.startswith(module + ".")]
            if len(local) == 1:
                return local[0], "direct"
            return resolved, "external"
        if not isinstance(call.func, ast.Attribute):
            return "<dynamic>.<call>", "dynamic"
        method = call.func.attr
        owner = call.func.value
        if isinstance(owner, ast.Name) and owner.id in {"self", "cls"} and function and function.class_name:
            target = self._method_in_class(function.class_name, method)
            return (target.qualified_name if target else "%s::%s" % (function.class_name, method)), "virtual"
        if (
            isinstance(owner, ast.Call)
            and isinstance(owner.func, ast.Name)
            and owner.func.id == "super"
            and function
            and function.class_name
        ):
            class_info = self.classes.get(function.class_name)
            target = self._method_in_bases(class_info, method) if class_info else None
            return (target.qualified_name if target else "<dynamic>::%s" % method), "super"
        owner_name = _expr_name(owner)
        if isinstance(owner, ast.Name) and owner.id in types:
            target = self._method_in_class(types[owner.id], method)
            return (target.qualified_name if target else "%s::%s" % (types[owner.id], method)), "typed-receiver"
        if (
            isinstance(owner, ast.Attribute)
            and function
            and function.class_name
            and isinstance(owner.value, ast.Name)
            and owner.value.id == "self"
        ):
            class_info = self.classes.get(function.class_name)
            property_type = class_info.properties.get(owner.attr) if class_info else None
            if property_type:
                target = self._method_in_class(property_type, method)
                return (target.qualified_name if target else "%s::%s" % (property_type, method)), "typed-property"
        if isinstance(owner, ast.Call):
            owner_target, _ = self._resolve_call(owner, function, imports, types, module)
            owner_info = self.functions.get(owner_target)
            if owner_info and owner_info.return_type:
                target = self._method_in_class(owner_info.return_type, method)
                return (
                    target.qualified_name if target else "%s::%s" % (owner_info.return_type, method)
                ), "chained-return"
        resolved_owner = _resolve_reference(owner_name, module, imports)
        target = self._method_in_class(resolved_owner, method)
        if target:
            return target.qualified_name, "qualified"
        direct = "%s.%s" % (resolved_owner, method)
        if direct in self.functions:
            return direct, "module"
        if resolved_owner in self.classes:
            return "%s::%s" % (resolved_owner, method), "class"
        return ("%s.%s" % (resolved_owner, method) if resolved_owner else "<dynamic>::%s" % method), "external"

    def _method_in_class(
        self, class_name: str, method: str, visited: Optional[Set[str]] = None
    ) -> Optional[FunctionInfo]:
        seen = visited or set()
        if class_name in seen:
            return None
        seen.add(class_name)
        direct = self.functions.get("%s::%s" % (class_name, method))
        if direct:
            return direct
        info = self.classes.get(class_name)
        if not info:
            return None
        for base in info.bases:
            found = self._method_in_class(base, method, seen)
            if found:
                return found
        return None

    def _method_in_bases(self, info: Optional[ClassInfo], method: str) -> Optional[FunctionInfo]:
        if not info:
            return None
        for base in info.bases:
            target = self._method_in_class(base, method)
            if target:
                return target
        return None

    def emit_endpoints(self, scan_files: Sequence[ParsedPythonFile]) -> None:
        options = self.request.get("options") if isinstance(self.request.get("options"), dict) else {}
        preset = self.request.get("staticExtractPresetRules")
        if preset is None:
            preset = options.get("staticExtractPresetRules", False)
        rule_sources = self.request.get("ruleSources") if isinstance(self.request.get("ruleSources"), list) else []
        rule_texts = self.request.get("ruleTexts") if isinstance(self.request.get("ruleTexts"), list) else []
        report = run_static_extract(
            project=str(self.project_root),
            ast_files=self.load_files,
            rule_sources=[str(item) for item in list(rule_sources) + list(rule_texts)],
            include_framework_presets=preset is True,
        )
        for fact in report.results:
            if fact.project_file_path not in self.scan_paths:
                continue
            self._add_endpoint(fact)

    def _add_endpoint(self, fact: ExtractedFact) -> None:
        endpoint_type = _endpoint_type(fact)
        if not endpoint_type:
            return
        direction = "inbound" if fact.fields.get("direction", "").lower() == "inbound" else "outbound"
        identity_value = _endpoint_identity(endpoint_type, fact.fields)
        if not identity_value:
            return
        match_identity = "%s:%s" % (endpoint_type, identity_value)
        identity = endpoint_id(direction, endpoint_type, match_identity)
        base: Dict[str, object] = {
            "id": identity,
            "name": match_identity,
            "qualifiedName": identity,
            "language": "python",
            "projectName": self.project_name,
            "projectFilePath": fact.project_file_path,
            "gitRepoUrl": self.request.get("gitRepoUrl"),
            "gitBranch": self.request.get("gitBranch"),
            "startLine": fact.start_line,
            "endLine": fact.end_line,
            "endpointType": endpoint_type,
            "direction": direction,
            "isExternal": direction == "outbound",
            "parseLevel": fact.fields.get("parseLevel", "full"),
            "matchIdentity": match_identity,
            "other": fact.fields.get("other"),
        }
        if endpoint_type == "HTTP":
            method = fact.fields.get("method", "ANY").upper()
            path = fact.fields.get("path", "")
            base.update(
                {"endpointKind": "http", "httpMethod": method, "path": path, "normalizedPath": path}
            )
        elif endpoint_type == "REDIS":
            base.update(
                {
                    "endpointKind": "redis",
                    "command": fact.fields.get("command"),
                    "keyPattern": fact.fields.get("keyPattern") or fact.fields.get("key"),
                }
            )
        elif endpoint_type == "MQ":
            base.update(
                {
                    "endpointKind": "mq",
                    "topic": fact.fields.get("topic"),
                    "operation": fact.fields.get("operation"),
                    "brokerType": fact.fields.get("brokerType"),
                }
            )
        else:
            base.update(
                {
                    "endpointKind": "db",
                    "tableName": fact.fields.get("tableName") or fact.fields.get("table"),
                    "dbOperation": fact.fields.get("dbOperation"),
                }
            )
        self.endpoints.setdefault(identity, base)
        function = self._endpoint_function(fact, direction)
        if function:
            function_node_id = function_id(function.qualified_name + "()")
            self._relationship(
                identity if direction == "inbound" else function_node_id,
                "ENDPOINT_TO_FUNCTION" if direction == "inbound" else "FUNCTION_TO_ENDPOINT",
                function_node_id if direction == "inbound" else identity,
                fact.start_line,
            )
        elif direction == "inbound" and fact.fields.get("handler"):
            base["parseLevel"] = "unresolved"
            file = self.files_by_path.get(fact.project_file_path)
            if file:
                function_node_id = self._put_placeholder_function(
                    file,
                    fact.fields["handler"],
                    ["endpoint-handler"],
                )
                self._relationship(
                    identity,
                    "ENDPOINT_TO_FUNCTION",
                    function_node_id,
                    fact.start_line,
                )
            self.diagnostics.append(
                {
                    "level": "WARN",
                    "code": "python.endpoint.handler.unresolved",
                    "message": "Route handler %s was not found in the parsed project" % fact.fields["handler"],
                    "projectFilePath": fact.project_file_path,
                    "lineNumber": fact.start_line,
                    "details": {"handler": fact.fields["handler"], "endpointId": identity},
                }
            )

    def _endpoint_function(self, fact: ExtractedFact, direction: str) -> Optional[FunctionInfo]:
        reference = (
            fact.enclosing_symbol[:-2]
            if fact.enclosing_symbol and fact.enclosing_symbol.endswith("()")
            else fact.enclosing_symbol
        )
        if direction == "inbound":
            reference = fact.fields.get("handler") or reference
        if not reference:
            return None
        direct = self.functions.get(reference)
        if direct:
            return direct
        if "::" in reference:
            owner, method = reference.rsplit("::", 1)
            return self._method_in_class(owner, method)
        candidates = self.functions_by_simple.get(reference.split(".")[-1], [])
        exact = [self.functions[name] for name in candidates if name == reference]
        if len(exact) == 1:
            return exact[0]
        suffix = [self.functions[name] for name in candidates if name.endswith(reference)]
        return suffix[0] if len(suffix) == 1 else None

    def _relationship(
        self,
        from_id: str,
        relationship_type: str,
        to_id: str,
        line: Optional[int] = None,
        call_type: Optional[str] = None,
    ) -> None:
        relationship_type = {
            "EXTENDS": "INHERITS",
            "IMPLEMENTS": "CONFORMS",
        }.get(relationship_type, relationship_type)
        contracts = {
            "PACKAGE_TO_UNIT": ("CodePackage", "CodeUnit"),
            "UNIT_TO_FUNCTION": ("CodeUnit", "CodeFunction"),
            "CALLS": ("CodeFunction", "CodeFunction"),
            "INHERITS": ("CodeUnit", "CodeUnit"),
            "CONFORMS": ("CodeUnit", "CodeUnit"),
            "OVERRIDES": ("CodeFunction", "CodeFunction"),
            "ENDPOINT_TO_FUNCTION": ("CodeEndpoint", "CodeFunction"),
            "FUNCTION_TO_ENDPOINT": ("CodeFunction", "CodeEndpoint"),
        }
        from_node_type, to_node_type = contracts[relationship_type]
        key = "%s|%s|%s" % (from_id, relationship_type, to_id)
        if key in self.relationships:
            return
        relationship: Dict[str, object] = {
            "id": relationship_id(from_id, relationship_type, to_id),
            "fromNodeId": from_id,
            "toNodeId": to_id,
            "relationshipType": relationship_type,
            "fromNodeType": from_node_type,
            "toNodeType": to_node_type,
            "language": "python",
            "projectName": self.project_name,
        }
        if line is not None:
            relationship["lineNumber"] = line
        if call_type:
            relationship["callType"] = call_type
        self.relationships[key] = relationship

    def delta(self) -> Dict[str, object]:
        return {
            "scope": {
                "projectName": self.project_name,
                "language": "python",
                "gitRepoUrl": self.request.get("gitRepoUrl"),
                "gitBranch": self.request.get("gitBranch"),
                "projectRoot": str(self.project_root),
                "sourceFiles": sorted(self.scan_paths),
                "changeType": self.request.get("changeType"),
                "attributes": {
                    "parser": "python-ast",
                    "parserRuntime": "%d.%d.%d" % sys.version_info[:3],
                    "minimumParserRuntime": "3.12",
                    "moduleCodeUnits": True,
                    "staticTypeBinding": "annotations-imports-local-construction-and-return-types",
                    "runtimeImportExecution": False,
                },
            },
            "packages": list(self.packages.values()),
            "units": list(self.units.values()),
            "functions": list(self.output_functions.values()),
            "endpoints": list(self.endpoints.values()),
            "relationships": list(self.relationships.values()),
            "deletedNodeIds": [],
            "deletedRelationshipIds": [],
            "diagnostics": self.diagnostics,
        }


def _requested_files(project_root: Path, value: object) -> Set[str]:
    if not isinstance(value, list) or not value:
        return set()
    result: Set[str] = set()
    for item in value:
        path = Path(str(item))
        absolute = path.resolve() if path.is_absolute() else (project_root / path).resolve()
        try:
            result.add(absolute.relative_to(project_root).as_posix())
        except ValueError:
            continue
    return result


def _imports(file: ParsedPythonFile) -> ImportTable:
    table = ImportTable()
    for node in file.tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                table.symbols[alias.asname or alias.name.split(".")[0]] = alias.name
        elif isinstance(node, ast.ImportFrom):
            module = _absolute_import(file.module_name, node.module or "", node.level)
            for alias in node.names:
                if alias.name != "*":
                    table.symbols[alias.asname or alias.name] = ".".join(filter(None, [module, alias.name]))
    return table


def _absolute_import(current: str, imported: str, level: int) -> str:
    if level <= 0:
        return imported
    parts = current.split(".")[:-1]
    keep = max(0, len(parts) - level + 1)
    return ".".join(parts[:keep] + ([imported] if imported else []))


def _resolve_reference(value: str, module: str, imports: ImportTable) -> str:
    if not value:
        return ""
    first, dot, rest = value.partition(".")
    imported = imports.symbols.get(first)
    if imported:
        return imported + (("." + rest) if dot else "")
    return "%s.%s" % (module, value)


def _resolve_type(value: str, module: str, imports: ImportTable) -> str:
    if not value:
        return ""
    clean = re.sub(r"^(?:Optional|List|Sequence|Iterable|Type)\[(.*)\]$", r"\1", value)
    clean = clean.split("|")[0].strip().strip("'")
    builtins = {"str", "int", "float", "bool", "bytes", "dict", "list", "tuple", "set", "None", "Any"}
    if clean in builtins:
        return clean
    return _resolve_reference(clean, module, imports)


def _resolve_base_type(value: str, module: str, imports: ImportTable) -> str:
    """Resolve the declared superclass/protocol itself, not its generic arguments."""
    return _resolve_type(value.split("[", 1)[0], module, imports)


def _annotation_name(value: Optional[ast.AST]) -> str:
    if value is None:
        return ""
    if isinstance(value, ast.Name):
        return value.id
    if isinstance(value, ast.Attribute):
        left = _annotation_name(value.value)
        return "%s.%s" % (left, value.attr) if left else value.attr
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return value.value
    if isinstance(value, ast.Subscript):
        return "%s[%s]" % (_annotation_name(value.value), _annotation_name(value.slice))
    if isinstance(value, ast.Tuple):
        return ",".join(_annotation_name(item) for item in value.elts)
    if isinstance(value, ast.BinOp) and isinstance(value.op, ast.BitOr):
        return "%s|%s" % (_annotation_name(value.left), _annotation_name(value.right))
    return ""


def _class_properties(info: ClassInfo) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for node in info.node.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            result[node.target.id] = _resolve_type(_annotation_name(node.annotation), info.module, info.imports)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "__init__":
            parameter_types = {
                arg.arg: _resolve_type(_annotation_name(arg.annotation), info.module, info.imports)
                for arg in node.args.args
                if arg.annotation is not None
            }
            for child in ast.walk(node):
                if not isinstance(child, (ast.Assign, ast.AnnAssign)):
                    continue
                target = (
                    child.target if isinstance(child, ast.AnnAssign) else (child.targets[0] if child.targets else None)
                )
                value = child.value
                if (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                ):
                    if isinstance(child, ast.AnnAssign) and child.annotation:
                        result[target.attr] = _resolve_type(
                            _annotation_name(child.annotation), info.module, info.imports
                        )
                    elif isinstance(value, ast.Name) and value.id in parameter_types:
                        result[target.attr] = parameter_types[value.id]
                    elif isinstance(value, ast.Call):
                        result[target.attr] = _resolve_type(_expr_name(value.func), info.module, info.imports)
    return result


def _local_types(function: Optional[FunctionInfo], state: _State) -> Dict[str, str]:
    if not function:
        return {}
    result: Dict[str, str] = {}
    node = function.node
    for arg in list(node.args.posonlyargs) + list(node.args.args) + list(node.args.kwonlyargs):
        if arg.annotation:
            result[arg.arg] = _resolve_type(_annotation_name(arg.annotation), function.module, function.imports)
    for child in _walk_without_nested(node):
        if isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
            result[child.target.id] = _resolve_type(
                _annotation_name(child.annotation), function.module, function.imports
            )
        elif isinstance(child, ast.Assign) and isinstance(child.value, ast.Call):
            inferred = _resolve_type(_expr_name(child.value.func), function.module, function.imports)
            if inferred in state.classes:
                for target in child.targets:
                    if isinstance(target, ast.Name):
                        result[target.id] = inferred
    return result


def _walk_without_nested(root: ast.AST) -> Iterable[ast.AST]:
    stack = list(ast.iter_child_nodes(root))
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        stack.extend(ast.iter_child_nodes(node))


def _calls_without_nested_definitions(root: ast.AST) -> Iterable[ast.Call]:
    for node in _walk_without_nested(root):
        if isinstance(node, ast.Call):
            yield node


def _module_executable_nodes(tree: ast.Module) -> Iterable[ast.AST]:
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for decorator in getattr(node, "decorator_list", []):
                yield decorator
            continue
        yield node


def _expr_name(value: Optional[ast.AST]) -> str:
    if isinstance(value, ast.Name):
        return value.id
    if isinstance(value, ast.Attribute):
        left = _expr_name(value.value)
        return "%s.%s" % (left, value.attr) if left else value.attr
    return ""


def _assigned_lambdas(body: Sequence[ast.stmt]) -> Iterable[Tuple[str, ast.Lambda]]:
    """Yield statically named callables without executing project code."""
    for statement in body:
        target = ""
        value: Optional[ast.AST] = None
        if isinstance(statement, ast.Assign) and statement.targets:
            target = _expr_name(statement.targets[0])
            value = statement.value
        elif isinstance(statement, ast.AnnAssign):
            target = _expr_name(statement.target)
            value = statement.value
        if not target or value is None:
            continue
        if isinstance(value, ast.Lambda):
            yield target, value
        elif isinstance(value, ast.Dict):
            for key, item in zip(value.keys, value.values):
                if isinstance(item, ast.Lambda) and isinstance(key, ast.Constant) and isinstance(key.value, str):
                    yield "%s.%s" % (target, key.value), item


def _package_name(module: str, relative: str) -> str:
    if relative.endswith("/__init__.py"):
        return module
    return module.rsplit(".", 1)[0] if "." in module else "<root>"


def _node_base(
    state: _State,
    identity: str,
    name: str,
    qualified_name: str,
    file: ParsedPythonFile,
    node: Optional[ast.AST] = None,
) -> Dict[str, object]:
    result: Dict[str, object] = {
        "id": identity,
        "name": name,
        "qualifiedName": qualified_name,
        "language": "python",
        "projectName": state.project_name,
        "projectFilePath": file.project_file_path,
    }
    if state.request.get("gitRepoUrl"):
        result["gitRepoUrl"] = state.request.get("gitRepoUrl")
    if state.request.get("gitBranch"):
        result["gitBranch"] = state.request.get("gitBranch")
    if node is not None:
        result["startLine"] = getattr(node, "lineno", None)
        result["endLine"] = getattr(node, "end_lineno", getattr(node, "lineno", None))
    return result


def _function_node(
    state: _State,
    file: ParsedPythonFile,
    qualified_name: str,
    name: str,
    signature: str,
    modifiers: Sequence[str],
    return_type: Optional[str],
    is_async: bool,
    is_constructor: bool,
    node: Optional[ast.AST] = None,
) -> Dict[str, object]:
    result = _node_base(state, function_id(qualified_name + "()"), name, qualified_name + "()", file, node)
    result.update(
        {
            "signature": signature,
            "modifiers": list(modifiers),
            "isStatic": "static" in modifiers,
            "isAsync": is_async,
            "isConstructor": is_constructor,
            "isPlaceholder": False,
        }
    )
    if return_type:
        result["returnType"] = return_type
    return result


def _function_modifiers(node: ast.AST, name: str = "") -> List[str]:
    result: List[str] = []
    names = {
        _expr_name(item.func if isinstance(item, ast.Call) else item).split(".")[-1]
        for item in getattr(node, "decorator_list", [])
    }
    if "staticmethod" in names:
        result.append("static")
    if "classmethod" in names:
        result.append("class")
    if "abstractmethod" in names:
        result.append("abstract")
    if isinstance(node, ast.AsyncFunctionDef):
        result.append("async")
    if name.startswith("__") and name.endswith("__"):
        result.append("dunder")
    elif name.startswith("_"):
        result.append("private")
    else:
        result.append("public")
    return result


def _signature(node: ast.AST, name: str = "") -> str:
    arguments: List[str] = []
    positional = list(node.args.posonlyargs) + list(node.args.args)
    for arg in positional:
        annotation = _annotation_name(arg.annotation)
        arguments.append("%s%s" % (arg.arg, (": " + annotation) if annotation else ""))
    if node.args.vararg:
        arguments.append("*" + node.args.vararg.arg)
    for arg in node.args.kwonlyargs:
        annotation = _annotation_name(arg.annotation)
        arguments.append("%s%s" % (arg.arg, (": " + annotation) if annotation else ""))
    if node.args.kwarg:
        arguments.append("**" + node.args.kwarg.arg)
    return "%s(%s)" % (name or getattr(node, "name", "<lambda>"), ", ".join(arguments))


def _is_abstract_class(node: ast.ClassDef) -> bool:
    bases = {_annotation_name(base).split(".")[-1] for base in node.bases}
    return bool(bases & {"ABC", "Protocol"}) or any(
        isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        and "abstractmethod"
        in {_expr_name(dec.func if isinstance(dec, ast.Call) else dec).split(".")[-1] for dec in item.decorator_list}
        for item in node.body
    )


def _simple_function_name(value: str) -> str:
    base = value[:-2] if value.endswith("()") else value
    return re.split(r"::|\.", base)[-1]


def _endpoint_type(fact: ExtractedFact) -> str:
    explicit = fact.fields.get("endpointType", "").upper()
    if explicit in {"HTTP", "MQ", "REDIS", "DB"}:
        return explicit
    lowered = fact.fact_type.lower()
    if "http" in lowered or "route" in lowered or fact.fields.get("path"):
        return "HTTP"
    if "redis" in lowered or fact.fields.get("keyPattern"):
        return "REDIS"
    if "mq" in lowered or fact.fields.get("topic"):
        return "MQ"
    if "db" in lowered or fact.fields.get("tableName"):
        return "DB"
    return ""


def _endpoint_identity(endpoint_type: str, fields: Dict[str, str]) -> str:
    if endpoint_type == "HTTP":
        path = fields.get("path", "")
        return "%s:%s" % (fields.get("method", "ANY").upper(), path) if path else ""
    if endpoint_type == "REDIS":
        return fields.get("keyPattern") or fields.get("key", "")
    if endpoint_type == "MQ":
        return fields.get("topic", "")
    return fields.get("tableName") or fields.get("table", "")
