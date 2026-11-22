import os

def str_xor(secret, key):
    new_key = key
    i = 0
    while len(new_key) < len(secret):
        new_key = new_key + key[i]
        i = (i + 1) % len(key)
    return "".join([chr(ord(secret_c) ^ ord(new_key_c)) for (secret_c, new_key_c) in zip(secret, new_key)])

flag_enc = chr(0x13) + chr(0x01) + chr(0x17) + chr(0x07) + chr(0x2c) + chr(0x3a) + chr(0x2f) + chr(0x1a) + chr(0x0d) + chr(0x53) + chr(0x0c) + chr(0x47) + chr(0x0a) + chr(0x5f) + chr(0x5e) + chr(0x02) + chr(0x3e) + chr(0x5a) + chr(0x56) + chr(0x5d) + chr(0x45) + chr(0x5d) + chr(0x58) + chr(0x31) + chr(0x0d) + chr(0x58) + chr(0x0f) + chr(0x02) + chr(0x5a) + chr(0x10) + chr(0x0e) + chr(0x5d) + chr(0x13)

# Find codebook.txt relative to script location or current dir
script_dir = os.path.dirname(os.path.abspath(__file__))
codebook_path = os.path.join(script_dir, 'codebook.txt')
if not os.path.exists(codebook_path):
    codebook_path = 'codebook.txt'

with open(codebook_path, 'r') as f:
    codebook = f.read().strip()

password = codebook[4] + codebook[14] + codebook[13] + codebook[14] + \
           codebook[23] + codebook[25] + codebook[16] + codebook[0] + \
           codebook[25]

flag = str_xor(flag_enc, password)
print(flag)