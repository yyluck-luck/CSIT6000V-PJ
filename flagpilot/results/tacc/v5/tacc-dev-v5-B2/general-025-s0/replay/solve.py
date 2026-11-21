import re

with open('challenge/convertme.py', 'r') as f:
    content = f.read()

# Extract flag_enc hex values
match = re.search(r'flag_enc\s*=\s*(.+)', content)
flag_enc_line = match.group(1)
hex_vals = re.findall(r'0x([0-9a-fA-F]+)', flag_enc_line)
flag_enc = bytes([int(h, 16) for h in hex_vals])

# Extract key
key_match = re.search(r"str_xor\(flag_enc,\s*'([^']+)'", content)
key = key_match.group(1).encode()

# XOR with repeating key
new_key = key
i = 0
while len(new_key) < len(flag_enc):
    new_key = new_key + key[i:i+1]
    i = (i + 1) % len(key)

flag = ''.join([chr(a ^ b) for a, b in zip(flag_enc, new_key)])
print(flag)