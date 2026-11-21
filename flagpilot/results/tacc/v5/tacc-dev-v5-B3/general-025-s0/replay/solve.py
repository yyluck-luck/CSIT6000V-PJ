import re
src = open('./challenge/convertme.py').read()
m = re.search(r'flag_enc\s*=\s*(.+)', src)
flag_enc = eval(m.group(1))
key = re.search(r"str_xor\(flag_enc,\s*'([^']+)'", src).group(1)
def str_xor(secret, key):
    new_key = key
    i = 0
    while len(new_key) < len(secret):
        new_key = new_key + key[i]
        i = (i + 1) % len(key)
    return ''.join([chr(ord(s) ^ ord(k)) for s,k in zip(secret,new_key)])
print(str_xor(flag_enc, key))