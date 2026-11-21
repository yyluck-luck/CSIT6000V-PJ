import base64

with open('challenge/readmycert.csr', 'r') as f:
    content = f.read()

# Extract the base64 content
lines = content.strip().split('\n')
b64_content = ''.join(lines[1:-1])

# Decode base64
decoded = base64.b64decode(b64_content)

# Find the flag in the decoded bytes
idx = decoded.find(b'picoCTF')
end_idx = decoded.find(b'}', idx)
flag = decoded[idx:end_idx+1].decode('ascii')
print(flag)