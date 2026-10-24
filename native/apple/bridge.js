ObjC.import('Foundation');
function run() {
  let stage='input';
  try {
    const raw=$.NSFileHandle.fileHandleWithStandardInput.readDataToEndOfFile;
    const q=JSON.parse(ObjC.unwrap($.NSString.alloc.initWithDataEncoding(raw,$.NSUTF8StringEncoding)));
    const cal=Application('/System/Applications/Calendar.app'),rem=Application('/System/Applications/Reminders.app');
    if(q.action==='inventory') {
      stage='calendar inventory';
      const events=cal.calendars.name().map((name,index)=>({id:'name:'+name,title:name,kind:'event',writable:cal.calendars[index].writable(),source:'Apple Calendar (account not exposed by scripting)'}));
      stage='reminder inventory';
      const lists=[]; rem.accounts().forEach(a=>{a.lists().forEach(c=>lists.push({id:c.id(),title:c.name(),kind:'reminder',writable:true,source:a.name()}));});
      stage='default reminder list';
      return JSON.stringify({ok:true,calendars:events.concat(lists),default_reminder:rem.defaultList().id()});
    }
    if(!/^[a-f0-9-]{36}$/i.test(q.token||'')) throw Error('Invalid operation token.');
    const isReminder=q.kind==='reminder',app=isReminder?rem:cal;
    const targets=isReminder?rem.lists().filter(c=>c.id()===q.calendar_id):cal.calendars.name().map((name,index)=>({name,index})).filter(c=>'name:'+c.name===q.calendar_id).map(c=>cal.calendars[c.index]);
    if(targets.length!==1) throw Error('The configured Apple calendar or list is unavailable.');
    const target=targets[0],marker='[Julius:'+q.token+']';
    const matches=isReminder?target.reminders.whose({body:marker})():target.events.whose({description:marker})();
    if(q.action==='inspect') {
      const rows=isReminder?target.reminders.whose({id:q.remote_id})():target.events.whose({uid:q.remote_id})();
      if(rows.length!==1)throw Error('Linked Apple item could not be uniquely read.');
      const item=rows[0];
      if((isReminder?item.body():item.description())!==marker)throw Error('Apple item ownership changed.');
      const alarmDate=isReminder?item.remindMeDate():null;
      return JSON.stringify({ok:true,title:isReminder?item.name():item.summary(),completed:isReminder?item.completed():null,due:(isReminder?item.dueDate():item.startDate()).getTime()/1000,end:isReminder?null:item.endDate().getTime()/1000,alerts:isReminder?(alarmDate&&typeof alarmDate.getTime==='function'?1:0):item.displayAlarms().length,alert_due:isReminder&&alarmDate&&typeof alarmDate.getTime==='function'?alarmDate.getTime()/1000:null});
    }
    if(q.action==='complete') {
      if(!isReminder)throw Error('Only reminders support completion.');
      const rows=target.reminders.whose({id:q.remote_id})();
      if(rows.length!==1)throw Error('Linked reminder could not be uniquely read.');
      const item=rows[0];
      if(item.body()!==marker)throw Error('Apple item ownership changed.');
      item.completed=true;
      if(!item.completed())throw Error('Apple completion could not be verified.');
      return JSON.stringify({ok:true,code:'done',message:'Linked Apple reminder marked completed.'});
    }
    if(q.action==='delete') {
      const candidates=isReminder?target.reminders.whose({id:q.remote_id})():target.events.whose({uid:q.remote_id})();
      if(candidates.length===0)return JSON.stringify({ok:true,code:'already_absent',message:'No linked Apple item remains.'});
      if(candidates.length!==1)throw Error('Apple item ownership is ambiguous.');
      const owned=candidates;
      if((isReminder?owned[0].body():owned[0].description())!==marker)throw Error('Apple item was changed; cancellation requires review.');
      app.delete(owned[0]);return JSON.stringify({ok:true,code:'deleted',message:'Owned Apple item removed.'});
    }
    if(q.action!=='create')throw Error('Unsupported Apple action.');
    if(matches.length===1)return JSON.stringify({ok:true,code:'duplicate',remote_id:isReminder?matches[0].id():matches[0].uid(),message:'Apple item already exists.'});
    if(matches.length>1)throw Error('Duplicate Apple items need review.');
    if(typeof q.title!=='string'||q.title.length<1||q.title.length>240||typeof q.due!=='number'||!isFinite(q.due))throw Error('Invalid Apple item.');
    const date=new Date(q.due*1000);
    let item;
    if(isReminder) {
      item=rem.Reminder({name:q.title,body:marker,dueDate:date,remindMeDate:date});target.reminders.push(item);
    } else {
      if(typeof q.end!=='number'||!isFinite(q.end)||q.end<=q.due)throw Error('Invalid event end.');
      item=cal.Event({summary:q.title,description:marker,startDate:date,endDate:new Date(q.end*1000)});target.events.push(item);
      item.displayAlarms.push(cal.DisplayAlarm({triggerInterval:0}));
    }
    return JSON.stringify({ok:true,code:'created',remote_id:isReminder?item.id():item.uid(),message:'Saved to Apple '+(isReminder?'Reminders with a due date.':'Calendar with a start alert.')});
  } catch(e) {return JSON.stringify({ok:false,code:'apple_error',message:stage+': '+String(e)});}
}
