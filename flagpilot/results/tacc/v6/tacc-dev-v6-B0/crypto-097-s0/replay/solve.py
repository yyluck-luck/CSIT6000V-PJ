import os
with open('./challenge/encrypted.txt') as f:
    s = f.read().strip()
# find shift that yields picoCTF prefix
for shift in range(26):
    out = ''.join(chr((ord(c)-97-shift)%26+97) if c.islower() else chr((ord(c)-65-shift)%26+65) if c.isupper() else c for c in s)
    if out.startswith('picoCTF{'):
        print(out)
        break
