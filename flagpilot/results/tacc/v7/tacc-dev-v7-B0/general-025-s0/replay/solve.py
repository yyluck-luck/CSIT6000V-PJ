import re
src = open('./challenge/convertme.py').read()
m = re.search(r'flag_enc\s*=\s*(.+)', src)
expr = m.group(1)
flag_enc = eval(expr)
km = re.search(r"str_xor\(flag_enc,\s*['\"](.+?)['\"]\)", src)
key = km.group(1)

def str_xor(secret, key):
    new_key = key
    i = 0
    while len(new_key) < len(secret):
        new_key = new_key + key[i]
        i = (i + 1) % len(key)
    return ''.join([chr(ord(secret_c) ^ ord(new_key_c)) for (secret_c,new_key_c) in zip(secret,new_key)])

print(str_xor(flag_enc, key))