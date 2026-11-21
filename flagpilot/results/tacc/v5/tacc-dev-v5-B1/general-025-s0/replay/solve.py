import re

with open('./challenge/convertme.py', 'r') as f:
    src = f.read()

# Extract flag_enc by evaluating the chr() expressions
flag_enc_match = re.search(r'flag_enc\s*=\s*(.+)', src)
flag_enc = eval(flag_enc_match.group(1))

# Extract the key used in str_xor
key_match = re.search(r'str_xor\(flag_enc,\s*[\'\"](.+?)[\'\"]\)', src)
key = key_match.group(1)

def str_xor(secret, key):
    new_key = key
    i = 0
    while len(new_key) < len(secret):
        new_key = new_key + key[i]
        i = (i + 1) % len(key)
    return ''.join([chr(ord(secret_c) ^ ord(new_key_c)) for (secret_c, new_key_c) in zip(secret, new_key)])

flag = str_xor(flag_enc, key)
print(flag)