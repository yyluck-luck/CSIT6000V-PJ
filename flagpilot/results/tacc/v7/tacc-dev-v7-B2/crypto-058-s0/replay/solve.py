import base64
csr = open('challenge/readmycert.csr').read()
lines = csr.strip().split('\n')
b64 = ''.join(lines[1:-1])
data = base64.b64decode(b64)
flag = data.decode('latin-1').split('picoCTF{')[1].split('}')[0]
print(f'picoCTF{{{flag}}}')