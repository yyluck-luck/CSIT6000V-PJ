import base64
import re

# Read the CSR file
with open('./challenge/readmycert.csr', 'r') as f:
    content = f.read()

# Extract the base64 content between the BEGIN and END markers
match = re.search(r'-----BEGIN CERTIFICATE REQUEST-----(.*?)-----END CERTIFICATE REQUEST-----', content, re.DOTALL)
b64_data = match.group(1).strip()

# Decode the base64
decoded = base64.b64decode(b64_data)

# Search for the flag pattern in the decoded bytes
flag_match = re.search(rb'picoCTF\{[a-zA-Z0-9_]+\}', decoded)
if flag_match:
    print(flag_match.group(0).decode('utf-8'))
else:
    # Fallback: extract all printable strings and look for picoCTF
    strings = re.findall(rb'[\x20-\x7e]{10,}', decoded)
    for s in strings:
        if b'picoCTF' in s:
            print(s.decode('utf-8'))
            break