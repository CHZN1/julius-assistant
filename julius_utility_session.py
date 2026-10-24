"""Explicit human commands own reminder/task/event writes; models get read tools."""
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from julius_planning import output
from julius_conversions import local_datetime

class UtilitySession:
    def __init__(self, planner, timezone_name='America/Los_Angeles', provider=None):
        self.planner=planner
        self.timezone_name=timezone_name
        self.pending=None
        self.provider=provider

    def _add(self, kind, title, due=None, end=None, zone=None):
        out=self.planner.add(kind,title,due,end)
        if out['ok'] and due is not None:
            from datetime import timezone
            utc=datetime.fromtimestamp(due,timezone.utc).isoformat()
            local=datetime.fromtimestamp(due,ZoneInfo(zone or self.timezone_name)).strftime('%Y-%m-%d %I:%M %p %Z')
            out['message']=out['message'].replace(utc,local)
        if out['ok'] and kind in {'event','reminder'} and self.provider is not None and self.provider.enabled:
            external=self.provider.sync(out['data']['id'],kind,title,due,end)
            out['data']['apple']=external
            out['message'] += ' '+external['message']
            if not external.get('ok'):
                out['code']='partial_sync'
                out['message']+=' The local item remains saved; Apple delivery is not confirmed.'
        if out['ok'] and kind=='reminder' and due is not None:
            spoken=f"Okay. I'll remind you to {title} at {local}."
            if out['code']=='partial_sync':spoken+=' Saved locally, but Apple delivery is not confirmed. Details are on screen.'
            elif out['data'].get('apple',{}).get('ok'):spoken+=' Saved to Apple Reminders.'
            out['data']['speech_message']=spoken
        return out

    def handle(self, text, turn):
        t=text.strip().rstrip('.!?')
        low=t.lower()
        pending=self.pending
        self.pending=None  # Unrelated turns cannot leave a generic yes armed.
        if pending and low in {'yes','yes please','confirm','cancel','no'}:
            if low in {'cancel','no'}:return output(True,'cancelled','Calendar draft discarded. No event was added.')
            if turn<=pending['turn'] or self.planner.clock()-pending['created']>900:
                return output(False,'expired','That calendar preview has expired. Please request it again.')
            return self._add('event',pending['title'],pending['due'],pending['end'],pending.get('zone'))
        try:
            reminder=re.fullmatch(r'remind me in (\d+(?:\.\d+)?) (seconds?|minutes?|hours?|days?) to (.+)',t,re.I)
            timer=re.fullmatch(r'(?:set|start) (?:a )?timer for (\d+(?:\.\d+)?) (seconds?|minutes?|hours?)(?: (?:for|called) (.+))?',t,re.I)
            if reminder or timer:
                match=reminder or timer; amount,unit,title=match.groups()
                seconds=float(amount)*{'second':1,'minute':60,'hour':3600,'day':86400}[unit.rstrip('s').lower()]
                if not 0<seconds<=366*86400:raise ValueError('Choose a duration greater than zero and no more than a year.')
                return self._add('reminder', title or 'Timer finished',self.planner.clock()+seconds)
            relative=re.fullmatch(r'remind me (today|tomorrow) at (\d{1,2})(?::(\d{2}))?\s*(am|pm)? to (.+)',t,re.I)
            if relative:
                day,hour,minute,period,title=relative.groups();hour=int(hour);minute=int(minute or 0)
                if minute>59 or hour>23 or period and not 1<=hour<=12:raise ValueError('Please use a valid clock time.')
                if not period and hour<=12:raise ValueError('Please specify AM or PM for that clock time.')
                if period:hour=hour%12+(12 if period.lower()=='pm' else 0)
                local=datetime.fromtimestamp(self.planner.clock(),ZoneInfo(self.timezone_name))
                target=local.replace(tzinfo=None,hour=hour,minute=minute,second=0,microsecond=0)+timedelta(days=day.lower()=='tomorrow')
                due=local_datetime(target.isoformat(),self.timezone_name).timestamp()
                return self._add('reminder',title,due)
            dated=re.fullmatch(r'remind me at (\S+)(?: in (\S+))? to (.+)',t,re.I)
            if dated:
                stamp,zone,title=dated.groups()
                due=local_datetime(stamp,zone or self.timezone_name).timestamp()
                return self._add('reminder',title,due,zone=zone or self.timezone_name)
            task=re.fullmatch(r'(?:add (?:a )?task(?: to)?|add to (?:my )?task list) (.+)',t,re.I)
            if task:return self.planner.add('task',task.group(1))
            listed=re.fullmatch(r'(?:list|show)(?: my)? (tasks|reminders|timers|calendar|events)',low)
            if listed:
                kind={'tasks':'task','reminders':'reminder','timers':'reminder','calendar':'event','events':'event'}[listed.group(1)]
                return self.planner.list(kind)
            finish=re.fullmatch(r'(complete task|mark task|complete reminder|mark reminder|cancel task|cancel reminder|cancel timer|cancel event) (.+?)(?: done)?',t,re.I)
            if finish:
                command,name=finish.groups();command=command.lower()
                kind='task' if 'task' in command else 'event' if 'event' in command else 'reminder'
                status='done' if command.startswith(('complete','mark')) else 'cancelled'
                if kind in {'event','reminder'} and self.provider is not None and self.provider.enabled:
                    with self.planner.db() as con:
                        rows=con.execute("SELECT id,title FROM items WHERE kind=? AND (status='pending' OR (?='done' AND kind='reminder' AND status='delivered')) LIMIT 1001",(kind,status)).fetchall()
                    matches=[r for r in rows if r['id']==name.strip('"') or r['title'].casefold()==name.strip('"').casefold()]
                    if len(matches)==1:
                        remote=(self.provider.complete if status=='done' else self.provider.cancel)(matches[0]['id'])
                        if not remote.get('ok'):return remote
                return self.planner.finish_named(kind,name,status)
            event=re.fullmatch(r'(?:schedule|create) (?:a )?(?:calendar )?event "([^"\n]+)" from (\S+) to (\S+)(?: in (\S+))?',t,re.I)
            if event:
                title,start,end,zone=event.groups();zone=zone or self.timezone_name
                a,b=local_datetime(start,zone),local_datetime(end,zone)
                if a.timestamp()<=self.planner.clock() or not a.timestamp()<b.timestamp()<=a.timestamp()+31*86400:
                    raise ValueError('Use a future start and an end after it, within 31 days.')
                self.pending={'title':title,'due':a.timestamp(),'end':b.timestamp(),'turn':turn,'created':self.planner.clock(),'zone':zone}
                destination='Julius and the configured Apple calendar' if self.provider is not None and self.provider.config.get('event') else 'Julius only; Apple Calendar is not configured'
                return output(True,'preview',f'Calendar preview: {title}, {a.isoformat()} to {b.isoformat()}. Destination: {destination}. Say yes to confirm or cancel.',preview=self.pending.copy())
            if re.match(r'(?:remind me|(?:set|start) (?:a )?timer|add (?:a )?task|cancel (?:task|reminder|timer|event)|(?:complete|mark) (?:task|reminder)|(?:schedule|create) (?:a )?(?:calendar )?event)',low):
                return output(False,'clarification','Please specify an exact duration or ISO date/time for reminders, a task title, or an exact item reference. Calendar events need a quoted title, start and end; include a time zone when needed.')
        except (ValueError,OverflowError) as exc:
            return output(False,'invalid_request',str(exc))
        return None

    def deliver_due(self, speak):
        delivered=[]
        for _ in range(3):
            claimed=self.planner.claim_due(limit=1)
            if not claimed:break
            item=claimed[0]
            late=self.planner.clock()-item['due']>60
            message=('Missed reminder: ' if late else 'Reminder: ')+item['title']
            try:
                speak(message)
            except Exception:
                self.planner.retry(item['id'],item['lease'])
                raise
            if self.planner.acknowledge(item['id'],item['lease']):delivered.append(item['id'])
        return delivered
