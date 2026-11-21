with open('./challenge/encrypted.txt') as f:
    s = f.read().strip()
shift = 18
out = []
for c in s:
    if c.isalpha():
        base = ord('A') if c.isupper() else ord('a')
        out.append(chr((ord(c)-base+shift)%26+base))
    else:
        out.append(c)
print(''.join(out))
