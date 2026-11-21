import base64
import re

with open('./challenge/readmycert.csr', 'r') as f:
    content = f.read()

# Extract the base64 content between the BEGIN and END markers
lines = content.strip().split('\n')
b64_content = ''.join(lines[1:-1])

# Decode the full CSR
raw = base64.b64decode(b64_content)

# The CN field in the subject contains the flag
# Search for 'picoCTF' in the raw bytes
idx = raw.find(b'picoCTF')
if idx != -1:
    end = raw.find(b'}', idx)
    flag = raw[idx:end+1].decode()
    print(flag)
else:
    match = re.search(r'cGljb0NURns[A-Za-z0-9+/=]+', b64_content)
    if match:
        flag = base64.b64decode(match.group()).decode()
        print(flag)