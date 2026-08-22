import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from code_graph_parser_python import PythonCodeGraphParser
from code_graph_parser_python.ids import relationship_id


class PythonParserTest(unittest.TestCase):
    def fixture(self, files):
        root = Path(tempfile.mkdtemp(prefix="code-graph-parser-python-"))
        for relative, source in files.items():
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(source, encoding="utf-8")
        return root

    def parse(self, root, **extra):
        request = {"projectName": "demo", "language": "python", "projectRoot": str(root), "sourceFiles": []}
        request.update(extra)
        return PythonCodeGraphParser().parse(request)

    def test_structure_calls_inheritance_and_placeholders(self):
        root = self.fixture(
            {
                "app/base.py": """from typing import Protocol
class Gateway(Protocol):
    def send(self) -> str: ...
class Base:
    def run(self) -> None: pass
""",
                "app/service.py": """from .base import Base, Gateway
def helper() -> None: pass
class Service(Base):
    def __init__(self, gateway: Gateway):
        self.gateway = gateway
    def run(self) -> None:
        helper()
        self.gateway.send()
        unknown.call()
""",
            }
        )
        delta = self.parse(root)
        functions = {item["qualifiedName"]: item for item in delta["functions"]}
        self.assertIn("app.service.Service::run()", functions)
        self.assertIn("app.service.helper()", functions)
        self.assertTrue(any(item.get("isPlaceholder") for item in delta["functions"]))
        relationship_types = {item["relationshipType"] for item in delta["relationships"]}
        self.assertIn("PYTHON_INHERITS", relationship_types)
        self.assertIn("PYTHON_OVERRIDES", relationship_types)
        self.assertIn("CALLS", relationship_types)
        self.assertEqual(0, len(_dangling(delta)))

    def test_exact_extends_implements_and_overrides_bindings(self):
        root = self.fixture(
            {
                "contracts.py": """from typing import Protocol as Contract, TypeVar
T = TypeVar('T')
class Gateway(Contract[T]):
    def send(self, value: str) -> str: ...
class Partial(Contract):
    def first(self) -> None: ...
    def second(self) -> None: ...
class Base:
    def run(self) -> None: pass
""",
                "service.py": """from contracts import Base, Gateway, Partial
class Service(Base):
    def run(self) -> None: pass
class GatewayService(Gateway):
    def send(self, value: str) -> str: return value
class Almost(Partial):
    def first(self) -> None: pass
class Unrelated:
    def send(self, value: str) -> str: return value
""",
            }
        )
        delta = self.parse(root)
        relationships = {
            (item["fromNodeId"], item["relationshipType"], item["toNodeId"]): item
            for item in delta["relationships"]
        }
        expected = {
            ("unit:service.Service", "PYTHON_INHERITS", "unit:contracts.Base"),
            ("unit:service.GatewayService", "PYTHON_CONFORMS", "unit:contracts.Gateway"),
            ("unit:service.Almost", "PYTHON_CONFORMS", "unit:contracts.Partial"),
            ("fn:service.Service::run()", "PYTHON_OVERRIDES", "fn:contracts.Base::run()"),
            (
                "fn:service.GatewayService::send()",
                "PYTHON_OVERRIDES",
                "fn:contracts.Gateway::send()",
            ),
            ("fn:service.Almost::first()", "PYTHON_OVERRIDES", "fn:contracts.Partial::first()"),
        }
        self.assertTrue(expected.issubset(relationships.keys()), relationships.keys())
        self.assertNotIn(
            ("fn:service.Unrelated::send()", "PYTHON_OVERRIDES", "fn:contracts.Gateway::send()"),
            relationships,
        )
        for key in expected:
            item = relationships[key]
            self.assertEqual(relationship_id(*key), item["id"])
            expected_contract = (
                ("REFINES", "CodeFunction", "CodeFunction")
                if key[1] == "PYTHON_OVERRIDES"
                else (
                    "CONFORMS" if key[1] == "PYTHON_CONFORMS" else "SPECIALIZES",
                    "CodeUnit",
                    "CodeUnit",
                )
            )
            self.assertEqual(expected_contract[0], item["relationshipKind"])
            self.assertEqual(expected_contract[1], item["fromNodeType"])
            self.assertEqual(expected_contract[2], item["toNodeType"])
        self.assertEqual(0, len(_dangling(delta)))

    def test_fastapi_flask_and_django_endpoints_link_handlers(self):
        root = self.fixture(
            {
                "api.py": """from fastapi import FastAPI
app = FastAPI()
@app.get('/users/{user_id}')
def show(user_id: str): pass
""",
                "flask_app.py": """from flask import Flask
app = Flask(__name__)
@app.post('/orders')
def create_order(): pass
""",
                "project/urls.py": "from django.urls import path\nfrom users.views import health\nurlpatterns = [path('health/', health)]\n",
                "users/views.py": "def health(request): pass\n",
            }
        )
        delta = self.parse(root, options={"staticExtractPresetRules": True})
        identities = {item["matchIdentity"] for item in delta["endpoints"]}
        self.assertEqual({"HTTP:GET:/users/{param}", "HTTP:POST:/orders", "HTTP:ANY:/health"}, identities)
        links = [item for item in delta["relationships"] if item["relationshipType"] == "ENDPOINT_TO_FUNCTION"]
        self.assertEqual(3, len(links))

    def test_django_class_view_and_drf_router_use_bound_or_placeholder_handlers(self):
        root = self.fixture(
            {
                "project/urls.py": """from django.urls import include, path
from users.views import ProfileView
urlpatterns = [path('profile/', ProfileView.as_view()), path('api/', include('users.api.urls'))]
""",
                "users/views.py": "from django.views import View\nclass ProfileView(View): pass\n",
                "users/api/urls.py": """from rest_framework.routers import DefaultRouter
from .views import WidgetViewSet
router = DefaultRouter()
router.register('widgets', WidgetViewSet)
urlpatterns = router.urls
""",
                "users/api/views.py": """from rest_framework.viewsets import ModelViewSet
class WidgetViewSet(ModelViewSet):
    def list(self, request): pass
""",
            }
        )
        delta = self.parse(root, options={"staticExtractPresetRules": True})
        identities = {item["matchIdentity"] for item in delta["endpoints"]}
        self.assertIn("HTTP:ANY:/profile", identities)
        self.assertIn("HTTP:GET:/api/widgets", identities)
        self.assertIn("HTTP:DELETE:/api/widgets/{param}", identities)
        links = [item for item in delta["relationships"] if item["relationshipType"] == "ENDPOINT_TO_FUNCTION"]
        self.assertEqual(len(delta["endpoints"]), len(links))
        self.assertTrue(
            any(
                item.get("isPlaceholder") and "endpoint-handler" in item.get("modifiers", [])
                for item in delta["functions"]
            )
        )
        self.assertEqual(0, len(_dangling(delta)))

    def test_incremental_scope_emits_only_requested_file(self):
        root = self.fixture({"a.py": "def a(): pass\n", "b.py": "def b(): pass\n"})
        delta = self.parse(root, sourceFiles=[str(root / "a.py")])
        self.assertEqual(["a.py"], delta["scope"]["sourceFiles"])
        self.assertEqual({"a.py"}, {node["projectFilePath"] for node in delta["units"] + delta["functions"]})

    def test_stdio_contract(self):
        root = self.fixture({"app.py": "def run(): pass\n"})
        request = json.dumps(
            {"projectName": "stdio", "language": "python", "projectRoot": str(root), "sourceFiles": []}
        )
        process = subprocess.run(
            [sys.executable, "-m", "code_graph_parser_python.cli", "--stdio"],
            input=request,
            text=True,
            capture_output=True,
            cwd=str(Path(__file__).resolve().parents[1]),
            check=False,
        )
        self.assertEqual(0, process.returncode, process.stderr)
        delta = json.loads(process.stdout)
        self.assertEqual("python", delta["scope"]["language"])
        self.assertEqual([], delta["deletedNodeIds"])

    def test_no_ser_still_emits_basic_graph(self):
        root = self.fixture({"service.py": "def run():\n    return 1\n"})
        delta = self.parse(root, options={"staticExtractPresetRules": False})
        self.assertIn("fn:service.run()", {item["id"] for item in delta["functions"]})
        self.assertEqual([], delta["endpoints"])

    def test_configured_lambda_method_links_endpoint_and_preserves_other(self):
        root = self.fixture(
            {"handlers.py": "handler = lambda: 'ok'\nhandlers = {'save': lambda: 'saved'}\n"}
        )
        rule = '''rule "Configured lambda"
fact http_route
find method [handler,save]
let path =
  from method take value
let handler =
  from method take reference
build {
  endpointType: "HTTP"
  direction: "inbound"
  method: "POST"
  path: path | normalize httpPath
  handler: handler
  other: "source=caller-ser"
}
dict {
  handlers.handler() = /handler/{handlerId}
  handlers.handlers.save() = /save/{saveId}
}
'''
        delta = self.parse(
            root,
            ruleTexts=[rule],
            options={"staticExtractPresetRules": False},
        )
        function_ids = {item["id"] for item in delta["functions"]}
        self.assertIn("fn:handlers.handler()", function_ids)
        self.assertIn("fn:handlers.handlers::save()", function_ids)
        self.assertEqual(
            {"HTTP:POST:/handler/{handlerId}", "HTTP:POST:/save/{saveId}"},
            {item["matchIdentity"] for item in delta["endpoints"]},
        )
        self.assertEqual({"source=caller-ser"}, {item["other"] for item in delta["endpoints"]})
        links = [item for item in delta["relationships"] if item["relationshipType"] == "ENDPOINT_TO_FUNCTION"]
        self.assertEqual(2, len(links))

    def test_standard_endpoint_identities_exclude_other_metadata(self):
        root = self.fixture(
            {
                "service.py": """def run():
    http.get('/health')
    mq.send('orders')
    redis.get('user:*')
    db.query('users')
"""
            }
        )
        rules = []
        cases = [
            ("HTTP", "get", "http", "path", "method: \"GET\""),
            ("MQ", "send", "mq", "topic", "operation: \"PRODUCE\""),
            ("REDIS", "get", "redis", "keyPattern", "command: \"GET\""),
            ("DB", "query", "db", "tableName", "dbOperation: \"QUERY\""),
        ]
        for endpoint_type, call, owner, identity_field, extra in cases:
            rules.append(
                f'''rule "{endpoint_type} identity"
fact {owner}_endpoint
find call {call}
when call owner {owner}
let identity =
  from argument[0] take value
build {{
  endpointType: "{endpoint_type}"
  direction: "outbound"
  {extra}
  {identity_field}: identity
  other: "ignored-by-identity"
}}
'''
            )
        delta = self.parse(
            root,
            ruleSources=rules,
            options={"staticExtractPresetRules": False},
        )
        self.assertEqual(
            {"HTTP:GET:/health", "MQ:orders", "REDIS:user:*", "DB:users"},
            {item["matchIdentity"] for item in delta["endpoints"]},
        )
        self.assertEqual({"ignored-by-identity"}, {item["other"] for item in delta["endpoints"]})
        links = [item for item in delta["relationships"] if item["relationshipType"] == "FUNCTION_TO_ENDPOINT"]
        self.assertEqual(4, len(links))


def _dangling(delta):
    ids = {node["id"] for key in ("packages", "units", "functions", "endpoints") for node in delta[key]}
    return [item for item in delta["relationships"] if item["fromNodeId"] not in ids or item["toNodeId"] not in ids]


if __name__ == "__main__":
    unittest.main()
