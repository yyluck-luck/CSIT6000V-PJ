import base64

with open('challenge/readmycert.csr', 'r') as f:
    content = f.read()

lines = content.strip().split('\n')
b64_content = ''.join(lines[1:-1])

decoded = base64.b64decode(b64_content)

idx = decoded.find(b'picoCTF')
end_idx = decoded.find(b'}', idx)
flag = decoded[idx:end_idx+1].decode('utf-8')
print(flag)