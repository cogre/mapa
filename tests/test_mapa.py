import os, sys, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import mapa  # noqa: E402


class SampleProject(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.g = mapa.build(os.path.join(HERE, "sample"))
        cls.edges = {(e["from"], e["to"], e["type"]): e for e in cls.g["edges"]}

    def has(self, a, b, kind):
        self.assertIn((a, b, kind), self.edges)
        return self.edges[(a, b, kind)]

    def test_files_and_table_found(self):
        self.assertEqual(self.g["errors"], [])
        for node in ("core/db.py", "report.py", "table:orders", "core/db.py::Store.save_order"):
            self.assertIn(node, self.g["nodes"])

    def test_imports(self):
        self.has("report.py", "core/db.py", "imports")

    def test_call_through_imported_name_is_exact(self):
        e = self.has("report.py::run", "core/db.py::connect", "calls")
        self.assertEqual(e["certainty"], "EXACT")

    def test_call_on_unknown_object_is_inferred(self):
        e = self.has("report.py::run", "core/db.py::Store.save_order", "calls")
        self.assertEqual(e["certainty"], "INFERRED")

    def test_call_on_self(self):
        self.has("core/db.py::Store.unused_helper", "core/db.py::Store.save_order", "calls")

    def test_sql_links(self):
        self.has("core/db.py", "table:orders", "creates")
        self.has("core/db.py::Store.save_order", "table:orders", "writes")
        self.has("report.py::total", "table:orders", "reads")

    def test_queries(self):
        self.assertIn("core/db.py::connect", mapa.explain(self.g, "run"))
        self.assertIn("report.py::total", mapa.table(self.g, "orders"))
        self.assertTrue(mapa.path(self.g, "never_called", "orders").startswith("Path"))
        self.assertIn("never_called", mapa.report(self.g))

    def test_code_is_not_executed(self):
        self.assertNotIn("core.db", sys.modules)


if __name__ == "__main__":
    unittest.main()
