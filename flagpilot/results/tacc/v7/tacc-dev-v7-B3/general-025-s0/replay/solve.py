import re

with open('./challenge/convertme.py') as f:
    content = f.read()

# Extract the flag_enc bytes from the chr(0x..) calls
m = re.search(r'flag_enc\s*=\s*(.+)', content)
expr = m.group(1)
bytes_list = [int(x, 16) for x in re.findall(r'0x([0-9a-fA-F]+)', expr)]
flag_enc = bytes(bytes_list)

def str_xor(secret, key):
    new_key = key
    i = 0
    while len(new_key) < len(secret):
        new_key = new_key + key[i]
        i = (i + 1) % len(key)
    return ''.join([chr(ord(secret_c) ^ ord(new_key_c)) for (secret_c, new_key_c) in zip(secret, new_key)])

print(str_xor(flag_enc.decode('latin-1'), 'enkidu'))