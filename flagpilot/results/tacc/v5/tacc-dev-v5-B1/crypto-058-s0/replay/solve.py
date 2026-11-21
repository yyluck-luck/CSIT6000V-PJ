import base64, re
with open('./challenge/readmycert.csr') as f:
    data = f.read()
b64 = re.search(r'-----BEGIN CERTIFICATE REQUEST-----(.*?)-----END CERTIFICATE REQUEST-----', data, re.DOTALL).group(1).strip()
raw = base64.b64decode(b64)
for i in range(len(raw)):
    if raw[i:i+8] == b'picoCTF{':
        end = raw.find(b'}', i)
        print(raw[i:end+1].decode())
        break