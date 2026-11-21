import re
src = open('./challenge/convertme.py').read()
m = re.search(r'flag_enc\s*=\s*(.+)', src)
expr = m.group(1)
flag_enc = eval(expr, {'chr': chr})
m2 = re.search(r"str_xor\(flag_enc,\s*['\"]([^'\"]+)['\"]\)", src)
key = m2.group(1)
new_key = key
i = 0
while len(new_key) < len(flag_enc):
    new_key = new_key + key[i]
    i = (i + 1) % len(key)
flag = ''.join([chr(ord(a) ^ ord(b)) for a,b in zip(flag_enc, new_key)])
print(flag)