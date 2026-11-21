import base64

with open('./challenge/readmycert.csr', 'r') as f:
    content = f.read()

lines = content.strip().split('\n')
b64_data = ''.join(lines[1:-1])
der = base64.b64decode(b64_data)

idx = der.find(b'picoCTF')
end = der.find(b'}', idx)
flag = der[idx:end+1].decode('utf-8')
print(flag)