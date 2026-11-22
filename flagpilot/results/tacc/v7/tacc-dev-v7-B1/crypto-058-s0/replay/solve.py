import base64, re
with open('./challenge/readmycert.csr') as f:
    content = f.read()
b64 = re.search(r'-----BEGIN CERTIFICATE REQUEST-----(.+?)-----END CERTIFICATE REQUEST-----', content, re.DOTALL).group(1).replace('\n','')
data = base64.b64decode(b64)
for i in range(len(data)):
    if data[i:i+7] == b'picoCTF':
        end = data.find(b'}', i)
        print(data[i:end+1].decode())
        break