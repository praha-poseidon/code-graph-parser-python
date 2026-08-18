import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from code_graph_parser_python import PythonCodeGraphParser


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
        self.assertIn("EXTENDS", relationship_types)
        self.assertIn("OVERRIDES", relationship_types)
        self.assertIn("CALLS", relationship_types)
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
        delta = self.parse(root)
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
        delta = self.parse(root)
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


def _dangling(delta):
    ids = {node["id"] for key in ("packages", "units", "functions", "endpoints") for node in delta[key]}
    return [item for item in delta["relationships"] if item["fromNodeId"] not in ids or item["toNodeId"] not in ids]


if __name__ == "__main__":
    unittest.main()
