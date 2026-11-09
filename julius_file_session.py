"""Human-command controller for file previews, confirmations, and results."""
from julius_router import route, clean_asr
from julius_file_agent import result, FileProblem

import re
# Questions about the plan that is waiting for a yes: answered with the plan, which stays pending (an unrelated topic still drops it).
ABOUT_PENDING=re.compile(r"^(?:(?:so|and|well|okay|ok|wait|hey)[\s,]+)*(?:did you (?:do|move|trash|delete|finish|go ahead)(?: it| that)?|have you (?:done|moved|trashed|deleted|finished)(?: it| that)?|(?:is|was) (?:it|that) (?:done|finished|moved|trashed)|has (?:it|that) (?:been )?(?:done|moved|trashed|deleted)|did (?:it|that) (?:work|happen)|what(?:'s| is) (?:it )?(?:waiting|pending)|what (?:are you|were you) (?:going|about) to (?:do|trash|move)|what (?:will|would) (?:this|that|it) (?:do|change|trash|move)|which files?(?: (?:is it|was that|are you|is that))?|are you sure|(?:read|say) (?:it|that|the plan) (?:back|again)|what was (?:the plan|that))\s*[?.!]*$",re.I)
# After "Please name the exact files...", a bare filename ("Oh, report.txt") completes the trash request.
FOLLOW_UP=re.compile(r"^(?:(?:oh|um|uh|er|no|well|it'?s|its|that'?s|i meant|i said|just|the)[\s,]+)*(?P<name>[\w.-]+\.[a-z0-9]{1,8})(?:\s+(?:in|from|on)\s+(?:my |the )?(?P<folder>.+?))?\s*[.!?]*$",re.I)
BARE_YES=re.compile(r"^(?:(?:yes|yeah|yep|yup|sure|absolutely|definitely)(?:[\s,]+(?:please|do it|go ahead))?|(?:please\s+)?do it|go ahead|please do|confirm(?:ed)?|yes please)\s*[.!]*$",re.I)
NOTHING_WAITING="There's nothing waiting for a yes right now. What would you like me to do?"
TRASHY=re.compile(r"^\W*(?:please\s+)?(?:trash|bin|delete|remove|throw away|get rid of)\b",re.I)
TRASH_PHRASE=re.compile(r"\b(?:in|into|to)\s+(?:the |my )?(?:trash(?: can)?|bin)\b",re.I)
FOLDER_WORDS=re.compile(r"\b(?:in|inside|from|on)\b",re.I)

class FileSession:
    def __init__(self, agent):
        self.agent=agent;self.pending=None;self.last=None;self.last_result=None;self.clarify_turn=None;self.suppress_idle_yes=False
        import julius_router
        standard={agent.root/n for n in ('Documents','Desktop','Downloads')}
        julius_router.EXTRA_FOLDERS.clear()
        for folder,mode in agent.modes.items():  # a folder added in [files.folders] can be named by voice ("in sandbox")
            if mode!='none' and folder not in standard and folder.is_dir():julius_router.EXTRA_FOLDERS[julius_router._key(folder.name)]=str(folder)

    def _fuzzy_trash(self,out,args,text,turn):
        """Exact name found nothing. Look for the one file that was probably meant, in the folder he named (or, if he named none, in every folder Julius may change)."""
        from julius_filename_fuzzy import closest
        named=bool(FOLDER_WORDS.search(TRASH_PHRASE.sub('',text)))
        try:first=self.agent.resolve(args['source'])
        except (FileProblem,OSError):return out
        folders=[first] if named else [f for f,mode in self.agent.modes.items() if mode=='write' and f.is_dir()]
        found=[]   # (folder, file name)
        for folder in folders:
            names=sorted(p.name for p in folder.iterdir() if p.is_file() and not p.name.startswith('.'))
            found+=[(folder,n) for n in closest(args['pattern'],text,names)]
        if len(found)==1:return self.agent.propose('trash',source=str(found[0][0]),pattern=found[0][1],origin='human',turn=turn)
        if len(found)>1:return result(False,'clarification',"I found "+' and '.join(f"{n} in {f.name}" for f,n in found[:3])+". Which one should I trash? Say the exact name and folder.")
        return out

    def handle(self,text,turn):
        import re
        if re.match(r'^(?:read document|extract text from|search document) ',text.strip(),re.I):
            self.pending=None
            return None
        action=route(text,self.agent.root/'Documents',pending=self.pending is not None,has_last=self.last is not None)
        if not action:
            repaired=clean_asr(text)
            if repaired and repaired!=text:
                action=route(repaired,self.agent.root/'Documents',pending=self.pending is not None,has_last=self.last is not None)
                if action:text=repaired
        if not action and self.pending and ABOUT_PENDING.match(text.strip()):
            base=re.sub(r'^(?:Not yet\. )+','',(self.last_result or {}).get('message','')) if self.pending==self.last else ''
            out=result(True,'pending_status',"Not yet. "+(base or 'A change is waiting for your yes.'));self.last_result=out
            return out
        if not action and self.clarify_turn is not None and turn-self.clarify_turn<=2:
            follow=FOLLOW_UP.match(clean_asr(text) or text)
            if follow:
                where=f" in {follow.group('folder')}" if follow.group('folder') else ''
                action=route(f"trash {follow.group('name')}{where}",self.agent.root/'Documents',pending=self.pending is not None,has_last=self.last is not None)
                if action:text=f"trash {follow.group('name')}{where}"
        if not action and not self.pending and not self.suppress_idle_yes and BARE_YES.match(text.strip()):
            self.clarify_turn=None;out=result(True,'nothing_pending',NOTHING_WAITING);self.last_result=out
            return out
        if not action:
            # A generic yes after an unrelated topic must not execute an old plan.
            self.pending=None;self.clarify_turn=None
            return None
        kind,args=action
        self.clarify_turn=turn if kind=='clarify' and TRASHY.match(clean_asr(text) or text) else None
        if kind=='clarify':
            self.pending=None
            out=result(False,'clarification',args['clarification'])
        elif kind in {'read','list','find'}:
            self.pending=None
            if kind=='read' and str(args.get('path','')).lower().endswith(('.pdf','.docx')):
                from julius_documents import read_document
                out=read_document(args['path'],file_agent=self.agent)
            else:
                out=getattr(self.agent,kind)(**args)
        elif kind in {'rename','create'}:
            if self.pending:self.agent.cancel(self.pending)
            if kind=='rename':
                try:
                    old=self.agent.resolve(args['path'])
                    from pathlib import Path
                    target=Path(args['destination'])
                    if not target.is_absolute() and len(target.parts)==1:args['destination']=str(old.parent/target)
                except (FileProblem,OSError):
                    pass  # The service returns the path error in its structured result.
            out=self.agent.propose(kind,**args,turn=turn)
            self.pending=out['data']['plan']['id'] if out['ok'] else None
            if self.pending:self.last=self.pending
        elif kind in {'move','copy'}:
            if self.pending:self.agent.cancel(self.pending)
            out=self.agent.propose(kind,source=args['source'],destination=args['destination'],pattern=args['pattern'],recursive=args.get('recursive',False),turn=turn)
            self.pending=out['data']['plan']['id'] if out['ok'] else None
            if self.pending:self.last=self.pending
        elif kind=='trash':
            if self.pending:self.agent.cancel(self.pending)
            out=self.agent.propose('trash',source=args['source'],pattern=args['pattern'],origin='human',turn=turn)
            if not out['ok'] and out.get('code') in ('no_matches','not_found','outside_scope') and not FOLDER_WORDS.search(TRASH_PHRASE.sub('',text)) and not any(c in args['pattern'] for c in '*?['):
                homes=[f for f,mode in self.agent.modes.items() if mode=='write' and f.is_dir()]   # no folder was named: look for that exact name in every folder Julius may change
                hits=[f for f in homes if self.agent.find(pattern=args['pattern'],source=str(f),recursive=False)['data'].get('items')]
                if len(hits)==1:out=self.agent.propose('trash',source=str(hits[0]),pattern=args['pattern'],origin='human',turn=turn)
                elif len(hits)>1:out=result(False,'clarification',f"I found {args['pattern']} in {' and '.join(f.name for f in hits)}. Say which folder, for example trash {args['pattern']} in {hits[0].name}.")
            if not out['ok'] and out.get('code') in ('no_matches','not_found') and not any(c in args['pattern'] for c in '*?['):
                out=self._fuzzy_trash(out,args,text,turn)   # the recognizer mangled the name ("TrashReport.TXT"): offer the real file, still behind a yes
            self.pending=out['data']['plan']['id'] if out['ok'] and 'plan' in out.get('data',{}) else None
            if self.pending:self.last=self.pending
        elif kind=='empty_trash':
            if self.pending:self.agent.cancel(self.pending)
            out=self.agent.propose_empty_trash(turn=turn)
            self.pending=out['data']['plan']['id'] if out['ok'] else None
            if self.pending:self.last=self.pending
        elif kind=='trash_info':
            self.pending=None;out=self.agent.trash_info()
        elif kind=='history':
            self.pending=None;out=self.agent.history(today=bool((args or {}).get('today')))
        elif kind=='approve':
            out=self.agent.execute(self.pending,confirmed=True,turn=turn);self.pending=None
        elif kind=='cancel':
            out=self.agent.cancel(self.pending);self.pending=None
        elif kind=='resume':
            out=self.agent.show(self.last)
            plan=out['data'].get('plan',{})
            if out['ok'] and plan.get('status')=='pending':
                self.pending=self.last
                out['message']+=' Say yes to confirm or cancel to discard.'
            else:self.pending=None
        else:
            self.pending=None;out=self.agent.undo()
        self.last_result=out
        return out
