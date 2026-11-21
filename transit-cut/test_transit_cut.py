import tempfile
import json
import shutil
import subprocess
import sys
import unittest
import zipfile
from pathlib import Path

from transit_cut import compare, load

DEMO = Path(__file__).with_name("demo")


class ClosureTests(unittest.TestCase):
    def test_run_downloaded_script_without_data_files(self):
        with tempfile.TemporaryDirectory() as folder:
            script = Path(folder) / "my transit demo.py"
            shutil.copyfile(Path(__file__).with_name("transit_cut.py"), script)
            run = subprocess.run([sys.executable, str(script)], capture_output=True,
                                 text=True, check=True, timeout=10)
            self.assertEqual(json.loads(run.stdout), compare(load(DEMO), "A", {"GREEN"}))

    def test_parallel_route_detour_and_directed_edges(self):
        result = compare(load(DEMO), "A", {"GREEN"})
        self.assertEqual((result["reachable_before"], result["reachable_after"]), (5, 4))
        self.assertEqual(result["lost_stops"], [{"id": "D", "name": "Observatory"}])
        self.assertEqual(result["longer_paths"], [
            {"id": "C", "name": "West Gate", "before_hops": 2, "after_hops": 4}
        ])

    def test_zip_and_multiple_closures(self):
        with tempfile.TemporaryDirectory() as folder:
            archive = Path(folder) / "feed.zip"
            with zipfile.ZipFile(archive, "w") as feed:
                for item in DEMO.glob("*.txt"):
                    feed.write(item, item.name)
            self.assertEqual(compare(load(archive), "A", {"GREEN", "BLUE"})["reachable_after"], 0)

    def test_unknown_route_errors(self):
        with self.assertRaisesRegex(ValueError, "Unknown route"):
            compare(load(DEMO), "A", {"TYPO"})

    def test_no_service_out_of_terminal(self):
        self.assertEqual(compare(load(DEMO), "D", set())["reachable_before"], 0)


if __name__ == "__main__":
    unittest.main()
