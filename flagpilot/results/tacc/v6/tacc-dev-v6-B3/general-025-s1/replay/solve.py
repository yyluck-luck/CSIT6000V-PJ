import re
src = open('./challenge/convertme.py').read()
# extract the flag_enc line
m = re.search(r'flag_enc\s*=\s*(.+)', src)
line = m.group(1)
vals = [int(x, 16) for x in re.findall(r'0x([0-9a-fA-F]+)', line)]
key = b'enkidu'
flag = bytes([vals[i] ^ key[i % len(key)] for i in range(len(vals))])
print(flag.decode())