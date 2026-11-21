with open('./challenge/encrypted.txt') as f:
    s=f.read().strip()
for k in range(26):
    out=''.join(chr((ord(c)-65-k)%26+65) if c.isupper() else chr((ord(c)-97-k)%26+97) if c.islower() else c for c in s)
    if out.startswith('picoCTF{'):
        print(out)
        break