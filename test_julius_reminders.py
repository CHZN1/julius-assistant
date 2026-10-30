import tempfile, unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from julius_planning import Planner
from julius_reminders import ReminderService, parse_when
from julius_utility_session import UtilitySession

TZ = 'America/Los_Angeles'
NOW = datetime(2026, 10, 29, 14, 0, tzinfo=ZoneInfo(TZ)).timestamp()


def at(phrase):
    return datetime.fromtimestamp(parse_when(phrase, NOW, TZ), ZoneInfo(TZ)).strftime('%m-%d %H:%M')


class ParseWhenTests(unittest.TestCase):
    def test_relative(self):
        self.assertEqual(at('in 20 minutes'), '10-29 14:20')
        self.assertEqual(at('in an hour'), '10-29 15:00')
        self.assertEqual(at('in half an hour'), '10-29 14:30')

    def test_clock_times(self):
        self.assertEqual(at('at 3:45 PM'), '10-29 15:45')
        self.assertEqual(at('at 1 pm'), '10-30 13:00')  # already passed today: next occurrence
        self.assertEqual(at('5 pm tomorrow'), '10-30 17:00')

    def test_day_parts_and_weekdays(self):
        self.assertEqual(at('tomorrow morning'), '10-30 09:00')
        self.assertEqual(at('tomorrow evening'), '10-30 18:00')
        self.assertEqual(at('tonight'), '10-29 20:00')
        self.assertEqual(at('friday at 9am'), '10-30 09:00')

    def test_unclear_or_past_times_are_refused(self):
        for phrase in ('whenever', 'at 3', 'this morning'):
            with self.assertRaises(ValueError):
                parse_when(phrase, NOW, TZ)


class ServiceTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.mkdtemp()
        self.planner = Planner(Path(folder), clock=lambda: NOW) if 'clock' in Planner.__init__.__code__.co_varnames else Planner(Path(folder))
        self.planner.clock = lambda: NOW
        self.text = 'remind me to call mom at 3:45 PM'
        self.service = ReminderService(UtilitySession(self.planner, TZ), lambda: self.text)

    def test_set_list_cancel(self):
        spoken = self.service.set('call mom', 'at 3:45 PM')
        self.assertIn('call mom', spoken)
        self.assertIn('3:45', spoken)
        self.assertIn('call mom', self.service.list())
        self.text = 'cancel my call mom reminder'
        self.assertIn('cancelled', self.service.cancel('call mom'))
        self.assertEqual(self.service.list(), 'You have no active reminders.')

    def test_requires_the_person_to_ask(self):
        self.text = 'what is on my calendar'
        self.assertIn('only set reminders', self.service.set('x', 'in 5 minutes'))
        self.assertEqual(self.service.list(), 'You have no active reminders.')

    def test_bad_time_is_explained(self):
        self.assertIn("couldn't tell", self.service.set('call mom', 'whenever'))


if __name__ == '__main__':
    unittest.main()
