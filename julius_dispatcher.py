"""Small synchronous specialist dispatcher; no models, background jobs or retries.

File writes stay in the trusted human FileSession. Network tools retain their
transport timeouts. A slow synchronous operation is reported as slow, never
claimed cancelled while it might still be running.
"""
from collections import deque
from datetime import datetime, timezone
import threading
import time
import uuid

OWNERS = {
    'read_document':'documents', 'list_planner_items':'planning', 'convert_units':'calculator', 'date_calculate':'time',
    'get_datetime': 'time', 'get_weather': 'weather', 'get_forecast': 'weather',
    'get_scores': 'sports', 'get_quote': 'markets', 'calculate': 'calculator',
    'web_search': 'research', 'get_news': 'news',
    'list_files': 'files', 'read_file': 'files', 'find_files': 'files',
}

class Dispatcher:
    def __init__(self, registry, schemas, file_agent=None, history_limit=64):
        self.registry = registry
        self.schemas = {s['function']['name']: s['function']['parameters'] for s in schemas}
        self.file_agent = file_agent
        self.history = deque(maxlen=history_limit)
        self._lock = threading.Lock()
        self.last_result = None
        self.turn_tasks = deque(maxlen=8)
        self._calls = 0
        self._turn_budget_active = False

    def begin_turn(self):
        self._turn_budget_active = True
        self.turn_tasks.clear()
        self._calls = 0
        self.last_result = None

    def _remember(self, task):
        metadata = {k: task[k] for k in ('id','agent','tool','started_at','status','code','seconds')}
        self.history.append(metadata)
        self.turn_tasks.append(metadata)
        self.last_result = task

    def _validate(self, name, args):
        if name not in OWNERS or name not in self.registry:
            raise ValueError(f'No tool called {name}. That utility is not available.')
        if not isinstance(args, dict):
            raise ValueError('Utility arguments must be an object.')
        schema = self.schemas[name]
        props = schema.get('properties', {})
        if set(args) - set(props):
            raise ValueError('Unexpected utility argument.')
        if any(k not in args for k in schema.get('required', [])):
            raise ValueError('A required utility argument is missing.')
        types = {'string': str, 'integer': int, 'boolean': bool, 'number': (int,float)}
        for key, value in args.items():
            typ = props[key].get('type')
            if typ in types and (not isinstance(value, types[typ]) or
                                 typ in {'integer','number'} and isinstance(value, bool)):
                raise ValueError(f'{key} has the wrong type.')
            if isinstance(value, str) and (not value.strip() or len(value) > 2048):
                raise ValueError(f'{key} must contain 1–2048 characters.')
        if name == 'get_forecast' and not 1 <= args.get('days', 7) <= 16:
            raise ValueError('Forecast length must be between 1 and 16 days.')
        if name == 'get_scores':
            if args.get('league', 'mlb').lower() not in {'mlb','nba','nfl','nhl','wnba'}:
                raise ValueError('That sports league is not supported yet.')
            if args.get('day', 'today').lower() not in {'today', 'yesterday'}:
                raise ValueError('Scores currently support today or yesterday only.')
        if name == 'get_quote':
            import re
            if not re.fullmatch(r'[A-Za-z0-9^.=-]{1,24}', args['symbol']):
                raise ValueError('Please provide a valid ticker symbol.')

    def dispatch(self, name, args=None):
        task = {'id': uuid.uuid4().hex, 'agent': OWNERS.get(name, 'unknown'),
                'tool': name, 'started_at': datetime.now(timezone.utc).isoformat(),
                'ok': False, 'status': 'failed', 'code': 'invalid_request', 'data': {}}
        started = time.perf_counter()
        try:
            args = {} if args is None else args
            self._validate(name, args)
            if self._turn_budget_active and self._calls >= 8:
                raise ValueError('Utility limit reached for this turn; please narrow the request.')
            self._calls += 1
            # One active utility avoids racing confirmations or adding model residency.
            if not self._lock.acquire(blocking=False):
                task.update(code='busy', message='Another utility is still working. Please try again when it finishes.')
            else:
                try:
                    if task['agent'] == 'files' and self.file_agent is not None:
                        method, kwargs = {
                            'list_files': ('list', {'directory': args.get('directory', '.')}),
                            'read_file': ('read', {'path': args.get('path')}),
                            'find_files': ('find', args),
                        }[name]
                        out = getattr(self.file_agent, method)(**kwargs)
                    else:
                        out = self.registry[name](**(args or {}))
                    if isinstance(out, dict) and 'ok' in out:
                        task.update(ok=out['ok'], code=out['code'], message=out['message'], data=out.get('data', {}))
                    else:
                        message = str(out)
                        failed = message.startswith(("Couldn't", 'No data for', 'No price for', 'No timezone data', "Don't have", 'Nothing found.'))
                        task.update(ok=not failed, code='unavailable' if failed else 'completed', message=message)
                    task['status'] = 'completed' if task['ok'] else 'failed'
                finally:
                    self._lock.release()
        except Exception as exc:
            task.update(code='timeout' if 'timeout' in type(exc).__name__.lower() else 'error',
                        message=f"{name} failed: {type(exc).__name__}: {exc}")
        task['seconds'] = round(time.perf_counter() - started, 4)
        # Keep metadata only: file contents and user queries do not linger here.
        self._remember(task)
        return task

    def human_file(self, session, text, turn):
        """Only the human controller can preview and approve mutations."""
        if not self._lock.acquire(blocking=False):
            from julius_file_agent import result
            return result(False, 'busy', 'Another utility is still working. Please try again when it finishes.')
        started = time.perf_counter()
        try:
            out = session.handle(text, turn)
            if out is not None:
                task = dict(out, id=uuid.uuid4().hex, tool='human_file',
                            started_at=datetime.now(timezone.utc).isoformat(),
                            status='partial' if out['code']=='partial_sync' else 'needs_confirmation' if out['code']=='preview' else 'needs_input' if out['code']=='clarification' else 'completed' if out['ok'] else 'failed',
                            seconds=round(time.perf_counter()-started, 4))
                self._remember(task)
            return out
        finally:
            self._lock.release()

    def human_utilities(self, session, text, turn):
        if not self._lock.acquire(blocking=False):
            from julius_planning import output
            return output(False,'busy','Another utility is still working. Please try again when it finishes.')
        started=time.perf_counter()
        try:
            try:
                out=session.handle(text,turn)
            except Exception as exc:
                from julius_planning import output
                out=output(False,'storage_error',f"The planner could not complete that request: {type(exc).__name__}: {exc}")
            if out is not None:
                task=dict(out,id=uuid.uuid4().hex,agent='planning',tool='human_planning',
                    started_at=datetime.now(timezone.utc).isoformat(),
                    status='partial' if out['code']=='partial_sync' else 'needs_confirmation' if out['code']=='preview' else 'needs_input' if out['code']=='clarification' else 'completed' if out['ok'] else 'failed',
                    seconds=round(time.perf_counter()-started,4))
                self._remember(task)
            return out
        finally:
            self._lock.release()

    def render(self, task):
        if task['agent'] == 'files' and task['data']:
            import julius_files as jf
            return jf.render(task)
        return task['message']
