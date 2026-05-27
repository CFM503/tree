import re, sys, os

base = os.path.dirname(os.path.abspath(__file__))
results = set()

for i in range(1, 8):
    fname = os.path.join(base, f'classes{"" if i==1 else i}.dex')
    with open(fname, 'rb') as f:
        data = f.read()
    matches = re.findall(rb'[\x20-\x7e]{8,300}', data)
    for m in matches:
        s = m.decode('ascii', 'ignore')
        sl = s.lower()
        if 'hyzhihuixing' in sl or 'wisdomtree' in sl or 'service/v2' in sl or 'login_v3' in sl:
            results.add(s)

for r in sorted(results):
    print(r)
