"""Small smoke benchmark, not a comprehensive coding score. No generated execution.
Outputs are checked against executable tests in a restricted AST-only subprocess.
"""
import ast,json,os,re,subprocess,sys,tempfile,time
from pathlib import Path
import ollama
CASES=[
('unique', 'Implement unique(values), returning distinct hashable values in original order. Empty list works.', [([3,1,3,2,1],[3,1,2]),([],[]),(['a','a','b'],['a','b'])]),
('find_first','Implement find_first(values, target) for a sorted list, returning the index of the FIRST target occurrence or -1 when absent, with binary search.',[(([1,2,2,2,4],2),1),(([],1),-1),(([1,3],2),-1),(([1,3,5],5),2)]),
('minutes','Implement minutes(value) for a string HH:MM. Return integer minutes since midnight. Require exactly two ASCII digits per component, hours 00-23 and minutes 00-59. Invalid strings return None.', [('00:00',0),('23:59',1439),('24:00',None),('9:05',None),('12:60',None),('ab:cd',None),('１２:３４',None)])]
# Execution harness excludes imports, attributes, dunder names and arbitrary calls.
HARNESS='''import ast,json,sys,resource
try:resource.setrlimit(resource.RLIMIT_CPU,(2,2));resource.setrlimit(resource.RLIMIT_DATA,(128*1024*1024,128*1024*1024))
except (ValueError,OSError):pass
x=json.load(sys.stdin);tree=ast.parse(x['code']);allowed={'len','range','enumerate','set','list','int','str','sorted','min','max','dict','zip','bool','isinstance','ValueError'}
for n in ast.walk(tree):
 if isinstance(n,(ast.Import,ast.ImportFrom,ast.ClassDef,ast.With,ast.Global,ast.Nonlocal)) or isinstance(n,ast.Name) and n.id.startswith('__'):raise ValueError('unsupported code structure')
 if isinstance(n,ast.Attribute) and n.attr not in {'append','add','split','isdigit','isascii'}:raise ValueError('unsupported attribute')
 if isinstance(n,ast.Call) and not (isinstance(n.func,ast.Name) and n.func.id in allowed|{x['name']} or isinstance(n.func,ast.Attribute) and n.func.attr in {'append','add','split','isdigit','isascii'}):raise ValueError('unsupported call')
ns={'__builtins__':{k:__builtins__.__dict__[k] for k in allowed}}
exec(compile(tree,'<coding-case>','exec'),ns)
passed=0
for args,expected in x['tests']:
 try:
  actual=ns[x['name']](*args) if x['name']=='find_first' else ns[x['name']](args)
  passed+=actual==expected
 except Exception:pass
print(json.dumps({'passed':passed,'total':len(x['tests'])}))
'''
def run_benchmark():
 rows=[]
 try:
  for model in ['llama3.2:3b','qwen3:8b']:
   for name,prompt,tests in CASES:
    start=time.perf_counter()
    response=ollama.Client(timeout=45).chat(model=model,messages=[{'role':'system','content':'Return only one Python function. No imports, attributes, helper classes, explanations or markdown. Use simple builtins and loops.'},{'role':'user','content':prompt}],think=False,options={'temperature':0,'num_ctx':2048,'num_predict':320},keep_alive='2m')
    code=response.message.content.strip();code=re.sub(r'^```(?:python)?\s*|\s*```$','',code)
    row={'model':model,'case':name,'seconds':time.perf_counter()-start,'code':code}
    try:
     proc=subprocess.run([sys.executable,'-I','-c',HARNESS],input=json.dumps({'name':name,'code':code,'tests':tests}),text=True,capture_output=True,timeout=4)
     row['verification']=json.loads(proc.stdout) if proc.returncode==0 else {'error':proc.stderr[-500:],'passed':0,'total':len(tests)}
    except Exception as exc:row['verification']={'error':str(exc),'passed':0,'total':len(tests)}
    rows.append(row);print(model,name,row['seconds'],row['verification'],flush=True)
    Path('benchmarks/2026-10-02/coding.json').write_text(json.dumps(rows,indent=2))
   ollama.generate(model=model,prompt='',keep_alive=0)
 finally:
  for model in ['llama3.2:3b','qwen3:8b']:
   try:ollama.generate(model=model,prompt='',keep_alive=0)
   except Exception:pass

if __name__=='__main__':run_benchmark()
