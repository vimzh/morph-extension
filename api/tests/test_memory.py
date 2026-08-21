"""Checks that project memory ignores one-off task instructions."""

import asyncio
import unittest

from utils.memory import PERSISTENCE_SIGNAL, extract_rules


class MemoryExtractionTests(unittest.TestCase):
    def test_task_local_instruction_does_not_call_model(self) -> None:
        rules = asyncio.run(
            extract_rules(
                [{"role": "user", "content": "Summarize this page in exactly three bullets."}],
                [],
            )
        )

        self.assertEqual(rules, [])

    def test_task_constraint_with_never_does_not_call_model(self) -> None:
        rules = asyncio.run(
            extract_rules(
                [{"role": "user", "content": "Hide promoted posts, but never hide ordinary posts."}],
                [],
            )
        )

        self.assertEqual(rules, [])

    def test_explicit_durable_directives_reach_extraction(self) -> None:
        self.assertIsNotNone(PERSISTENCE_SIGNAL.search("Always use TypeScript."))
        self.assertIsNotNone(PERSISTENCE_SIGNAL.search("Never use npm."))


if __name__ == "__main__":
    unittest.main()
