import re
src = open('challenge/code.py').read()
# extract flag_enc bytes
m = re.search(r'flag_enc = (.+)', src)
expr = m.group(1)
flag_enc = eval(expr)
codebook = open('challenge/codebook.txt').read().strip()
password = codebook[4] + codebook[14] + codebook[13] + codebook[14] + codebook[23] + codebook[25] + codebook[16] + codebook[0] + codebook[25]
new_key = password
i = 0
while len(new_key) < len(flag_enc):
    new_key = new_key + password[i]
    i = (i + 1) % len(password)
flag = ''.join(chr(ord(a) ^ ord(b)) for a, b in zip(flag_enc, new_key))
print(flag)