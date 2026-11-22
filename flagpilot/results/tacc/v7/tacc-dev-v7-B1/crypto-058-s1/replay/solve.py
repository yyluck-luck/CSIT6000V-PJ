import base64

csr = open('./challenge/readmycert.csr').read()
lines = csr.strip().split('\n')
b64 = ''.join(l for l in lines if not l.startswith('-----'))
raw = base64.b64decode(b64)
idx = raw.find(b'picoCTF')
end = raw.find(b'}', idx)
flag = raw[idx:end+1].decode()
print(flag)