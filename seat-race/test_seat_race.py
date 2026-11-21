import unittest
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier

from seat_race import change, desk, race, status


class BookingTests(unittest.TestCase):
    def test_run_file_without_arguments(self):
        script = Path(__file__).with_name("seat_race.py").resolve()
        with TemporaryDirectory() as folder:
            run = subprocess.run([sys.executable, str(script)], cwd=folder,
                                 capture_output=True, text=True, check=True, timeout=30)
            self.assertTrue(all(json.loads(run.stdout)["checks"].values()))
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_race_promotes_first_waiter(self):
        result = race(5, 2)
        self.assertEqual(len(result["before"]["confirmed"]), 2)
        self.assertEqual(result["cancellation"]["promoted"], result["before"]["waiting"][0])
        self.assertTrue(all(result["checks"].values()))

    def test_race_with_spare_capacity(self):
        result = race(2, 3)
        self.assertIsNone(result["cancellation"]["promoted"])
        self.assertEqual(len(result["after"]["confirmed"]), 1)

    def test_race_rejects_excessive_or_empty_inputs(self):
        for values in [(0, 1), (25, 1), (2, 0), (1, 25)]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                race(*values)

    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "seats.db"
        with desk(self.path) as db:
            change(db, "create", "lab", capacity=1)

    def test_many_claims_for_last_seat(self):
        barrier = Barrier(8)
        def claim(i):
            with desk(self.path) as db:
                barrier.wait(timeout=10)
                return change(db, "book", "lab", str(i))["state"]
        with ThreadPoolExecutor(max_workers=8) as pool:
            states = list(pool.map(claim, range(8)))
        self.assertEqual(states.count("confirmed"), 1)
        self.assertEqual(states.count("waiting"), 7)

    def test_fifo_and_cancel_waiter(self):
        with desk(self.path) as db:
            for student in ["A", "B", "C", "D"]:
                change(db, "book", "lab", student)
            self.assertTrue(change(db, "book", "lab", "B")["repeat"])
            self.assertIsNone(change(db, "cancel", "lab", "C")["promoted"])
            change(db, "book", "lab", "C")
            self.assertEqual(change(db, "cancel", "lab", "A")["promoted"], "B")
            self.assertEqual(status(db, "lab")["waiting"], ["D", "C"])

    def test_failed_change_rolls_back(self):
        with desk(self.path) as db:
            with self.assertRaises(ValueError):
                change(db, "create", "lab", capacity=2)
            with self.assertRaises(ValueError):
                change(db, "cancel", "lab", "nobody")
            self.assertEqual(change(db, "book", "lab", "A")["state"], "confirmed")


if __name__ == "__main__":
    unittest.main()
