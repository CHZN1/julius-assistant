import threading, time, unittest
from julius_dispatcher import Dispatcher


def _schema(name, props):
    return {"type": "function", "function": {"name": name, "parameters": {"type": "object", "properties": {k: {"type": "string"} for k in props}, "required": list(props)}}}


class ParallelDispatchTests(unittest.TestCase):
    def setUp(self):
        self.running = 0
        self.peak = 0
        self.guard = threading.Lock()

        def slow(**_):
            with self.guard:
                self.running += 1
                self.peak = max(self.peak, self.running)
            time.sleep(0.15)
            with self.guard:
                self.running -= 1
            return 'ok'
        self.registry = {'get_weather': slow, 'get_quote': slow, 'get_datetime': slow, 'read_document': slow, 'list_planner_items': slow}
        self.schemas = [_schema('get_weather', ['location']), _schema('get_quote', ['symbol']), _schema('get_datetime', []), _schema('read_document', ['path']), _schema('list_planner_items', [])]

    def test_lookups_run_concurrently_and_keep_order(self):
        d = Dispatcher(self.registry, self.schemas)
        start = time.perf_counter()
        tasks = d.dispatch_many([('get_weather', {'location': 'Springfield'}), ('get_quote', {'symbol': 'BTC'}), ('get_datetime', {})])
        self.assertLess(time.perf_counter() - start, 0.4)
        self.assertGreaterEqual(self.peak, 2)
        self.assertEqual([t['tool'] for t in tasks], ['get_weather', 'get_quote', 'get_datetime'])
        self.assertTrue(all(t['ok'] for t in tasks))

    def test_stateful_tools_stay_serial(self):
        d = Dispatcher(self.registry, self.schemas)
        tasks = d.dispatch_many([('read_document', {'path': 'a.pdf'}), ('list_planner_items', {})])
        self.assertEqual(self.peak, 1)
        self.assertTrue(all(t['ok'] for t in tasks))

    def test_one_failure_does_not_hide_the_others(self):
        d = Dispatcher(self.registry, self.schemas)
        tasks = d.dispatch_many([('get_weather', {'location': 'Springfield'}), ('get_quote', {})])
        self.assertEqual([t['ok'] for t in tasks], [True, False])


if __name__ == '__main__':
    unittest.main()
