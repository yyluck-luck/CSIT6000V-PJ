import re
src = open('./challenge/convertme.py').read()
m = re.search(r'flag_enc = (.*)', src)
flag_enc = eval(m.group(1))
key = b'enkidu'
out = bytes([ord(flag_enc[i]) ^ key[i % len(key)] for i in range(len(flag_enc))])
print(out.decode())