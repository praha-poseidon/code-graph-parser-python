# Real-project validation

The parser is validated without installing or starting the target applications. Both projects
were parsed with Python 3.12 and the built-in framework presets enabled.

| Project | Revision | Framework surface | Files | Real functions | Placeholders | CALLS | HTTP endpoints | Unlinked endpoints | Dangling relationships | Parse errors |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Open WebUI | `01f4282f1ffe0d6212f58d3afbeae21fffd0c4be` | FastAPI routers | 254 | 3,043 | 6,813 | 22,343 | 534 | 0 | 0 | 0 |
| NetBox | `d38ace89ce3e6fa0d0539e43baaa74cc376b4cf7` | Django + DRF ViewSets | 1,212 | 8,961 | 11,138 | 37,603 | 1,148 | 0 | 0 | 0 |
| Apache Superset | `097c99b19cb889061a7423fcc119c0637ce1203a` | Flask-AppBuilder | 2,570 | 24,507 | 20,274 | 99,801 | 385 | 0 | 0 | 0 |
| Saleor | `6ab90cd059b7a7d8cb7487dac1af00d89bdbcdb7` | Django + GraphQL gateway | 4,328 | 26,623 | 24,052 | 128,872 | 7 | 0 | 0 | 0 |
| Plane | `e056bbf9eb6b511cdc0a5823b1bd6922e561a485` | Django REST backend | 651 | 2,980 | 4,104 | 13,460 | 356 | 0 | 0 | 0 |

NetBox exercises nested `include()` prefixes, configuration base paths, decorated class views,
`router.register()`, standard ViewSet mixins, inherited custom `@action` routes, and project
helpers which construct URL lists dynamically. Framework-owned or dynamically generated target
methods are represented by stable placeholder functions, so the graph remains connected while
preserving the distinction from source-proven bindings.

Superset exercises Flask-AppBuilder `@expose`, inherited `resource_name`/`route_base`, and generated
ModelRestApi CRUD routes. Saleor verifies a GraphQL-native business backend where a small number of
HTTP gateways intentionally front a large in-process schema. Plane exercises hundreds of explicit,
nested Django business routes.

Example commands:

```bash
python3.12 bin/parser-python --project /repo/open-webui/backend --project-name open-webui --out /tmp/open-webui.json
python3.12 bin/parser-python --project /repo/netbox/netbox --project-name netbox --out /tmp/netbox.json
```
