import os
base = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'challenge')

def str_xor(secret, key):
    new_key = key
    i = 0
    while len(new_key) < len(secret):
        new_key = new_key + key[i]
        i = (i + 1) % len(key)
    return ''.join([chr(ord(a) ^ ord(b)) for a, b in zip(secret, new_key)])

flag_enc = ''.join(chr(c) for c in [0x13,0x01,0x17,0x07,0x2c,0x3a,0x2f,0x1a,0x0d,0x53,0x0c,0x47,0x0a,0x5f,0x5e,0x02,0x3e,0x5a,0x56,0x5d,0x45,0x5d,0x58,0x31,0x0d,0x58,0x0f,0x02,0x5a,0x10,0x0e,0x5d,0x13])

with open(os.path.join(base, 'codebook.txt')) as f:
    codebook = f.read()

password = codebook[4] + codebook[14] + codebook[13] + codebook[14] + codebook[23] + codebook[25] + codebook[16] + codebook[0] + codebook[25]
print(str_xor(flag_enc, password))
