# Real-project validation

The parser is validated without installing or starting the target applications. Both projects
were parsed with Python 3.12 and the built-in framework presets enabled.

| Project | Revision | Framework surface | Files | Real functions | Placeholders | CALLS | HTTP endpoints | Unlinked endpoints | Dangling relationships | Parse errors |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Open WebUI | `01f4282f1ffe0d6212f58d3afbeae21fffd0c4be` | FastAPI routers | 254 | 3,043 | 6,813 | 22,343 | 534 | 0 | 0 | 0 |
| NetBox | `d38ace89ce3e6fa0d0539e43baaa74cc376b4cf7` | Django + DRF ViewSets | 1,212 | 8,961 | 11,138 | 37,603 | 1,148 | 0 | 0 | 0 |

NetBox exercises nested `include()` prefixes, configuration base paths, decorated class views,
`router.register()`, standard ViewSet mixins, inherited custom `@action` routes, and project
helpers which construct URL lists dynamically. Framework-owned or dynamically generated target
methods are represented by stable placeholder functions, so the graph remains connected while
preserving the distinction from source-proven bindings.

Example commands:

```bash
python3.12 bin/code-graph-parser-python --project /repo/open-webui/backend --project-name open-webui --out /tmp/open-webui.json
python3.12 bin/code-graph-parser-python --project /repo/netbox/netbox --project-name netbox --out /tmp/netbox.json
```
