import base64
data = open('./challenge/readmycert.csr').read()
b64 = ''.join(l for l in data.splitlines() if 'BEGIN' not in l and 'END' not in l)
raw = base64.b64decode(b64)
idx = raw.find(b'\x06\x03\x55\x04\x03')
pos = idx + 5
length = raw[pos+1]
cn = raw[pos+2:pos+2+length]
print(cn.decode())
