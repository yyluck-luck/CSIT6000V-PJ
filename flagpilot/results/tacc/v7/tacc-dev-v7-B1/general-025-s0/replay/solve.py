import re

with open('./challenge/convertme.py', 'r') as f:
    content = f.read()

m = re.search(r'flag_enc\s*=\s*(.+)', content)
flag_enc_line = m.group(1).strip()

hex_vals = re.findall(r'chr\(0x([0-9a-fA-F]+)\)', flag_enc_line)
flag_enc = bytes([int(h, 16) for h in hex_vals])

m2 = re.search(r'str_xor\(flag_enc,\s*[\'\"](.+?)[\'\"]\)', content)
key = m2.group(1).encode()

def str_xor(secret, key):
    new_key = key
    i = 0
    while len(new_key) < len(secret):
        new_key = new_key + key[i:i+1]
        i = (i + 1) % len(key)
    return ''.join([chr(secret_c ^ new_key_c) for (secret_c, new_key_c) in zip(secret, new_key)])

print(str_xor(flag_enc, key))