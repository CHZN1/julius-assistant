"""Unit tests for the planner-required rule and the forced planner tool choice."""

import unittest

import julius.brain.tools as jt
from julius.brain import cloud


class PlannerRequiredTests(unittest.TestCase):
    def test_plans_comparisons_and_itineraries_go_to_the_planner(self):
        for text in (
            "Plan a three day trip to Tokyo, including the weather and what time it is there.",
            "Compare the weather in Springfield and Shelbyville.",
            "Compare the price of Bitcoin and Ethereum today and tell me which is doing better.",
            "Julius, please plan my weekend",
            "Can you compare these two laptops",
            "Write me an itinerary for Lisbon",
        ):
            self.assertTrue(jt.planner_required(text), text)

    def test_single_facts_and_look_alikes_do_not(self):
        for text in (
            "What's the weather in Springfield?",
            "Is Bitcoin up or down today?",
            "List my planner items",
            "What is the plan for today",
            "Set a reminder to compare notes at five",
            "",
        ):
            self.assertFalse(jt.planner_required(text), text)


class WeekdayQuestionTests(unittest.TestCase):
    def test_weekday_questions_need_the_date_tool(self):
        for text in (
            "What day of the week is March 3rd 2027?",
            "what day was July 4th 1999",
            "Which day will Christmas fall on, day of the week please",
        ):
            self.assertTrue(jt.weekday_question(text), text)

    def test_other_questions_do_not(self):
        for text in (
            "What's the weather today?",
            "Is it a good day for a walk",
            "How many days until May",
        ):
            self.assertFalse(jt.weekday_question(text), text)


class ForcedPlannerRequestTests(unittest.TestCase):
    def build(self, **flags):
        brain = cloud.CloudBrain.__new__(cloud.CloudBrain)
        brain.model = "m"
        brain._last_tools = None
        brain.tracker = type("T", (), {"cap_reached": lambda self: False})()
        messages = [{"role": "user", "content": "Plan a trip"}]
        return brain.request(messages, jt.SCHEMAS[:2], **flags)

    def test_force_planner_requires_the_planner_tool(self):
        kwargs = self.build(force_planner=True)
        self.assertEqual(kwargs["tool_choice"], {"type": "tool", "name": "escalate_to_planner"})

    def test_planner_wins_over_forcing_any_tool(self):
        kwargs = self.build(force_planner=True, force_tool=True)
        self.assertEqual(kwargs["tool_choice"]["name"], "escalate_to_planner")

    def test_force_tool_alone_still_means_any_tool(self):
        self.assertEqual(self.build(force_tool=True)["tool_choice"], {"type": "any"})

    def test_no_flags_leave_the_choice_to_the_model(self):
        self.assertNotIn("tool_choice", self.build())


if __name__ == "__main__":
    unittest.main()
