# code-graph-parser-python

Python 3.12+ process parser for `code-graph-engine`. It reads one `ParseRequest` JSON and emits
one GraphDelta JSON without importing or executing the target project.

Graph coverage includes Python packages/modules/classes/functions, `<module-init>`, calls,
inheritance, Protocol implementation, overrides, source-proven receiver binding, stable
placeholders, assigned lambdas, and caller-configured endpoint extraction. Framework preset
detection is available only through explicit `staticExtractPresetRules: true`; it is disabled by
default. With no SER, the basic graph is still emitted and endpoints are empty.

```bash
python3.12 -m unittest discover -s tests -v
parser-python --project /repo
echo '{"projectName":"demo","language":"python","projectRoot":"/repo","sourceFiles":[]}' \
  | parser-python --stdio
```

Engine configuration:

```bash
CODEGRAPH_PARSER_PROCESS_LANGUAGES=python
CODEGRAPH_PARSER_PYTHON_COMMAND="parser-python --stdio"
```

The parser never imports or runs the target application. Python 3.12 is the parser runtime so
one process can parse both older projects and current syntax such as `match`/`case`. See
[`VALIDATION.md`](VALIDATION.md) for pinned real-project results.

Endpoint `other` is an optional SER-produced string. HTTP, MQ, Redis, and DB identities use only
their standard path/topic/key/table fields; `other` never changes an endpoint ID.
