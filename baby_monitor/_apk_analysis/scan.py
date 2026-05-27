import re, sys, os

base = os.path.dirname(os.path.abspath(__file__))
results = set()

for i in range(1, 8):
    fname = os.path.join(base, f'classes{"" if i==1 else i}.dex')
    print(f"Scanning {fname}...", file=sys.stderr, flush=True)
    with open(fname, 'rb') as f:
        data = f.read()
    print(f"  size={len(data)}", file=sys.stderr, flush=True)
    matches = re.findall(rb'[\x20-\x7e]{8,300}', data)
    print(f"  strings={len(matches)}", file=sys.stderr, flush=True)
    for m in matches:
        s = m.decode('ascii', 'ignore')
        sl = s.lower()
        if 'hyzhihuixing' in sl or 'login' in sl:
            results.add(s)

for r in sorted(results):
    print(r)
