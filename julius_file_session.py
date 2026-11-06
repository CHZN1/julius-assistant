"""Human-command controller for file previews, confirmations, and results."""
from julius_router import route
from julius_file_agent import result, FileProblem

class FileSession:
    def __init__(self, agent):
        self.agent=agent;self.pending=None;self.last=None;self.last_result=None
        import julius_router
        standard={agent.root/n for n in ('Documents','Desktop','Downloads')}
        julius_router.EXTRA_FOLDERS.clear()
        for folder,mode in agent.modes.items():  # a folder added in [files.folders] can be named by voice ("in sandbox")
            if mode!='none' and folder not in standard and folder.is_dir():julius_router.EXTRA_FOLDERS[julius_router._key(folder.name)]=str(folder)

    def handle(self,text,turn):
        import re
        if re.match(r'^(?:read document|extract text from|search document) ',text.strip(),re.I):
            self.pending=None
            return None
        action=route(text,self.agent.root/'Documents',pending=self.pending is not None,has_last=self.last is not None)
        if not action:
            # A generic yes after an unrelated topic must not execute an old plan.
            self.pending=None
            return None
        kind,args=action
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
            self.pending=out['data']['plan']['id'] if out['ok'] else None
            if self.pending:self.last=self.pending
        elif kind=='empty_trash':
            if self.pending:self.agent.cancel(self.pending)
            out=self.agent.propose_empty_trash(turn=turn)
            self.pending=out['data']['plan']['id'] if out['ok'] else None
            if self.pending:self.last=self.pending
        elif kind=='trash_info':
            self.pending=None;out=self.agent.trash_info()
        elif kind=='history':
            self.pending=None;out=self.agent.history()
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
