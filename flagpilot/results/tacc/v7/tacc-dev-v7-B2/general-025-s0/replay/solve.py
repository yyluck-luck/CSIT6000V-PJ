import re

with open('challenge/convertme.py', 'r') as f:
    content = f.read()

# Extract flag_enc hex values
m = re.search(r'flag_enc\s*=\s*(.+)', content)
flag_enc_line = m.group(1)
hex_vals = re.findall(r'0x([0-9a-fA-F]+)', flag_enc_line)
flag_enc = ''.join(chr(int(h, 16)) for h in hex_vals)

# Extract key from str_xor call
m2 = re.search(r'str_xor\(flag_enc,\s*[\'\"]([^\'\"]+)[\'\"]\)', content)
key = m2.group(1)

# XOR decrypt
new_key = key
i = 0
while len(new_key) < len(flag_enc):
    new_key = new_key + key[i]
    i = (i + 1) % len(key)
flag = ''.join([chr(ord(a) ^ ord(b)) for a, b in zip(flag_enc, new_key)])
print(flag)