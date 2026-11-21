import base64

with open('./challenge/readmycert.csr', 'r') as f:
    content = f.read()

lines = content.strip().split('\n')
b64_lines = []
in_cert = False
for line in lines:
    if 'BEGIN CERTIFICATE REQUEST' in line:
        in_cert = True
        continue
    if 'END CERTIFICATE REQUEST' in line:
        in_cert = False
        continue
    if in_cert:
        b64_lines.append(line.strip())

b64_data = ''.join(b64_lines)
decoded = base64.b64decode(b64_data)

idx = decoded.find(b'picoCTF')
end = decoded.find(b'}', idx)
flag = decoded[idx:end+1].decode('utf-8')
print(flag)
