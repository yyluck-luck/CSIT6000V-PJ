import re

with open('./challenge/convertme.py') as f:
    content = f.read()

# Extract all chr(0xNN) values from the flag_enc line
m = re.search(r'flag_enc\s*=\s*(.+)', content)
flag_enc_line = m.group(1)
vals = [int(x, 16) for x in re.findall(r'0x([0-9a-fA-F]+)', flag_enc_line)]
flag_enc = ''.join(chr(v) for v in vals)

def str_xor(secret, key):
    new_key = key
    i = 0
    while len(new_key) < len(secret):
        new_key = new_key + key[i]
        i = (i + 1) % len(key)
    return ''.join([chr(ord(secret_c) ^ ord(new_key_c)) for (secret_c, new_key_c) in zip(secret, new_key)])

# Extract the key from the file
m2 = re.search(r"str_xor\(flag_enc,\s*'([^']+)'", content)
key = m2.group(1)

flag = str_xor(flag_enc, key)
print(flag)