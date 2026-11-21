import base64, re
with open('challenge/readmycert.csr') as f:
    content = f.read()
lines = content.strip().split('\n')
b64_data = ''.join(l for l in lines if not l.startswith('-----'))
decoded = base64.b64decode(b64_data)
for m in re.finditer(rb'picoCTF\{[^}]*\}', decoded):
    print(m.group().decode())
