import unittest

from agent_core.contract import Check, Expect
from agent_core.verify import check


class VerifyTests(unittest.TestCase):
    def test_all_pass(self):
        delta = {"enemies.e0.hp": -3, "player.energy": -1, "mode": "battle"}
        expect = Expect(
            (
                Check("enemies.e0.hp", "le", -1),
                Check("player.energy", "eq", -1),
                Check("mode", "eq", "battle"),
            )
        )
        self.assertEqual(check(delta, expect), [])

    def test_missing(self):
        failures = check({}, Expect((Check("player.energy", "eq", -1),)))
        self.assertEqual(failures, ["player.energy: missing"])

    def test_failed_comparison(self):
        failures = check({"player.energy": 0}, Expect((Check("player.energy", "eq", -1),)))
        self.assertEqual(len(failures), 1)
        self.assertIn("is false", failures[0])

    def test_empty_expect(self):
        self.assertEqual(check({"anything": 1}, Expect()), [])


if __name__ == "__main__":
    unittest.main()
