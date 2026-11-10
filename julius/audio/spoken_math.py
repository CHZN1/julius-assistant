"""Bounded spoken arithmetic parsing; explanatory text is never executed."""
import re
SMALL = dict(zip('zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen'.split(), range(20)))
TENS = dict(zip('twenty thirty forty fifty sixty seventy eighty ninety'.split(), range(20,100,10)))
OPERATORS = {'plus':'+','minus':'-','times':'*','multiplied by':'*','divided by':'/','+':'+','-':'-','*':'*','/':'/'}

def number(text):
    text=text.strip()
    if re.fullmatch(r'-?\d+(?:\.\d+)?',text):return text
    sign=''
    if text.startswith('negative '):sign='-';text=text[9:]
    words=text.replace('-',' ').split()
    if len(words)==1 and words[0] in SMALL:return sign+str(SMALL[words[0]])
    if words and words[0] in TENS:
        if len(words)==1:return sign+str(TENS[words[0]])
        if len(words)==2 and words[1] in SMALL and 1<=SMALL[words[1]]<=9:
            return sign+str(TENS[words[0]]+SMALL[words[1]])
    return None

def arithmetic_expression(text):
    if not isinstance(text,str) or len(text)>512:return None
    text=re.sub(r'^(?:what(?:\'s| is) |calculate )','',text.lower().strip().rstrip('?.!'))
    if number(text) is not None:return None
    percent=re.fullmatch(r'(.+?) ?(?:%|per ?cent) of (.+)',text.replace(',',''))
    if percent:
        part,whole=number(percent.group(1)),number(percent.group(2))
        return f'{part}/100*{whole}' if part is not None and whole is not None else None
    candidates=[]
    for match in re.finditer(r'(?<![a-z])(?:multiplied by|divided by|plus|minus|times)(?![a-z])|[+*/-]',text):
        left=number(text[:match.start()]);right=number(text[match.end():])
        if left is not None and right is not None:
            candidates.append(left+OPERATORS[match.group()]+right)
    return candidates[0] if len(candidates)==1 else None
