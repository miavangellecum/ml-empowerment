import traceback
from extraction.llm.extract import parse_receipt
p = r'C:\Users\miava\WebstormProjects\hackathon\temp_xtltl8t1fwnb1.jpg'
try:
    r = parse_receipt(p)
    print('PARSE_OK')
    print(r)
except Exception:
    traceback.print_exc()
