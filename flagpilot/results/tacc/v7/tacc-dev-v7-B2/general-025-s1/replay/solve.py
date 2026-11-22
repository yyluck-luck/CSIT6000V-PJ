import re

with open('challenge/convertme.py', 'r') as f:
    content = f.read()

# Extract flag_enc line
m = re.search(r'flag_enc\s*=\s*(.+)', content)
flag_enc_expr = m.group(1).strip()

# Evaluate the flag_enc expression safely
flag_enc = eval(flag_enc_expr)

# Extract key from str_xor call
m2 = re.search(r'str_xor\(flag_enc,\s*[\'\"](.+?)[\'\"]\)', content)
key = m2.group(1)

# Perform XOR decryption
new_key = key
i = 0
while len(new_key) < len(flag_enc):
    new_key = new_key + key[i]
    i = (i + 1) % len(key)

flag = ''.join([chr(ord(a) ^ ord(b)) for a, b in zip(flag_enc, new_key)])
print(flag)