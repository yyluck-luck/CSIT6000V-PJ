import base64

with open('./challenge/readmycert.csr', 'r') as f:
    content = f.read()

lines = content.strip().split('\n')
b64_content = ''.join(lines[1:-1])
decoded = base64.b64decode(b64_content)

for i in range(len(decoded)):
    if decoded[i:i+7] == b'picoCTF':
        end = decoded.find(b'}', i)
        if end != -1:
            flag = decoded[i:end+1].decode('utf-8')
            print(flag)
            break