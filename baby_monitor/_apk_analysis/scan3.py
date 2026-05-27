import re, os

base = os.path.dirname(os.path.abspath(__file__))

# Find LoginRequest fields and related class info
for i in range(1, 8):
    fname = os.path.join(base, f'classes{"" if i==1 else i}.dex')
    with open(fname, 'rb') as f:
        data = f.read()
    matches = re.findall(rb'[\x20-\x7e]{4,300}', data)
    for m in matches:
        s = m.decode('ascii', 'ignore')
        # Find login-related fields and methods
        if any(kw in s for kw in ['LoginRequest', 'LoginResult', 'login_v3', 'password', 'deviceType', 'deviceToken', 'clientType']):
            if 'Lcom/' not in s and 'Landroid' not in s and 'Lkotlin' not in s and 'Lokhttp3' not in s:
                if len(s) > 4 and len(s) < 200:
                    print(f'  {s}')
