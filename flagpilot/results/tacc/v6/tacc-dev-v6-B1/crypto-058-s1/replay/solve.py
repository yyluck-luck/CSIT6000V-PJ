import base64
import re

with open('./challenge/readmycert.csr', 'r') as f:
    content = f.read()

# Extract the base64 body between the BEGIN and END lines
match = re.search(r'-----BEGIN CERTIFICATE REQUEST-----(.*?)-----END CERTIFICATE REQUEST-----', content, re.DOTALL)
if not match:
    raise ValueError("No CSR found")

b64_body = match.group(1).strip()
raw = base64.b64decode(b64_body)

# Find the flag in the decoded bytes
idx = raw.find(b'picoCTF')
if idx == -1:
    raise ValueError("Flag not found in CSR")
end = raw.find(b'}', idx)
flag = raw[idx:end+1].decode()
print(flag)