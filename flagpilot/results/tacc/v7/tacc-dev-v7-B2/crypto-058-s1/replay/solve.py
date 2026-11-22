import base64

with open('challenge/readmycert.csr') as f:
    content = f.read()

lines = content.strip().split('\n')
b64_data = ''.join(lines[1:-1])
raw = base64.b64decode(b64_data)

idx = raw.find(b'picoCTF')
end = raw.find(b'}', idx)
flag = raw[idx:end+1].decode()
print(flag)