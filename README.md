# code-graph-parser-python

Python 3.12+ process parser for `code-graph-engine`. It reads one `ParseRequest` JSON and emits
one GraphDelta JSON without importing or executing the target project.

Graph coverage includes Python packages/modules/classes/functions, `<module-init>`, calls,
inheritance, Protocol implementation, overrides, source-proven receiver binding, stable
placeholders, and FastAPI/Flask/Django/Django REST Framework HTTP endpoints. Framework
detection is automatic; callers provide the existing project root/source files protocol and do
not select a framework or supply route aliases.

```bash
python3.12 -m unittest discover -s tests -v
python3.12 -m code_graph_parser_python.cli --project /repo
echo '{"projectName":"demo","language":"python","projectRoot":"/repo","sourceFiles":[]}' \
  | python3.12 -m code_graph_parser_python.cli --stdio
```

Engine configuration:

```bash
CODEGRAPH_PARSER_PROCESS_LANGUAGES=python
CODEGRAPH_PARSER_PYTHON_COMMAND="python3.12 -m code_graph_parser_python.cli --stdio"
```

The parser never imports or runs the target application. Python 3.12 is the parser runtime so
one process can parse both older projects and current syntax such as `match`/`case`. See
[`VALIDATION.md`](VALIDATION.md) for pinned real-project results.
