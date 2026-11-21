import re

with open('./challenge/convertme.py', 'r') as f:
    content = f.read()

# Extract flag_enc line
m = re.search(r'flag_enc\s*=\s*(.+)', content)
flag_enc_expr = m.group(1).strip()

# Evaluate the expression to get the string
flag_enc = eval(flag_enc_expr)

# Extract the key used in str_xor call
m2 = re.search(r'str_xor\(flag_enc,\s*[\'\"](.+?)[\'\"]\)', content)
key = m2.group(1)

def str_xor(secret, key):
    new_key = key
    i = 0
    while len(new_key) < len(secret):
        new_key = new_key + key[i]
        i = (i + 1) % len(key)
    return "".join([chr(ord(secret_c) ^ ord(new_key_c)) for (secret_c, new_key_c) in zip(secret, new_key)])

flag = str_xor(flag_enc, key)
print(flag)