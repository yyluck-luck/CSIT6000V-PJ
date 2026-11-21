s = open('./challenge/encrypted.txt').read().strip()
res = []
for c in s:
    if c.isalpha():
        base = ord('A') if c.isupper() else ord('a')
        res.append(chr((ord(c)-base-8) % 26 + base))
    else:
        res.append(c)
print(''.join(res))