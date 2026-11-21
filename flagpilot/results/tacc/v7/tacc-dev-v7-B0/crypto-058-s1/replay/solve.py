import base64

csr = open('./challenge/readmycert.csr').read()
lines = csr.strip().split('\n')
b64_content = ''.join(lines[1:-1])
raw = base64.b64decode(b64_content)
idx = raw.find(b'picoCTF')
end = raw.find(b'}', idx)
flag = raw[idx:end+1].decode()
print(flag)