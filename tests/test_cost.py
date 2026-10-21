"""cost.claude: a first call's input with prompt caching counted."""

import unittest

from walle import cost


class ClaudeCostTest(unittest.TestCase):
    def test_writes_and_reads_are_weighted_by_price(self):
        m = {"input": 1000, "cache_creation": 3000, "cache_write_1h": 1000, "cache_read": 10000}
        c = cost.claude(m, "claude-opus-5")
        # 1000 + 1.25 x 2000 (5-minute writes) + 2 x 1000 (1-hour writes) + 0.1 x 10000
        self.assertEqual(c["input_equiv"], 6500)
        self.assertEqual(c["usd"], round(6500 * 5.0 / 1e6, 4))
        self.assertEqual(c["priced_as"], "claude-opus-5")

    def test_the_read_rate_differs_by_model(self):
        m = {"input": 0, "cache_creation": 0, "cache_read": 100000}
        self.assertEqual(cost.claude(m, "claude-opus-5-5")["input_equiv"], 5000)
        self.assertEqual(cost.claude(m, "claude-fable-5-1")["input_equiv"], 2500)
        self.assertEqual(cost.claude(m, "claude-sonnet-4-6")["input_equiv"], 10000)

    def test_a_date_suffix_is_not_part_of_the_model(self):
        self.assertEqual(cost.claude({"input": 10}, "claude-haiku-4-5-20251001")["priced_as"], "claude-haiku-4-5")

    def test_an_unknown_model_gets_the_usual_read_rate_and_no_usd(self):
        c = cost.claude({"input": 0, "cache_creation": 0, "cache_read": 1000}, "claude-next-9")
        self.assertEqual((c["input_equiv"], c["usd"], c["priced_as"]), (100, None, None))

    def test_without_the_split_every_write_is_a_5_minute_write(self):
        self.assertEqual(cost.claude({"input": 0, "cache_creation": 800, "cache_read": 0}, "x")["input_equiv"], 1000)

    def test_a_long_haiku_prompt_costs_the_higher_tier(self):
        short = cost.claude({"input": 100000}, "claude-haiku-5-5")
        long = cost.claude({"input": 100001}, "claude-haiku-5-5")
        self.assertEqual(short["usd"], 0.01)
        self.assertEqual(long["usd"], round(100001 * 0.5 / 1e6, 4))


if __name__ == "__main__":
    unittest.main()
