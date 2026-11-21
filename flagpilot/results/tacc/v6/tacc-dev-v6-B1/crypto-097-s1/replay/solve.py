with open('./challenge/encrypted.txt') as f:
    s = f.read().strip()
for shift in range(1, 26):
    out = []
    for c in s:
        if c.isalpha():
            base = ord('A') if c.isupper() else ord('a')
            out.append(chr((ord(c) - base + shift) % 26 + base))
        else:
            out.append(c)
    t = ''.join(out)
    if 'picoCTF' in t:
        print(t)
        break
