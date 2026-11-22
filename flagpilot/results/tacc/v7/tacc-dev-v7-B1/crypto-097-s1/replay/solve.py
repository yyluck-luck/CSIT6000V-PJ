with open('./challenge/encrypted.txt') as f:
    s = f.read().strip()
result = []
for c in s:
    if c.isalpha():
        base = ord('A') if c.isupper() else ord('a')
        result.append(chr((ord(c) - base - 8) % 26 + base))
    else:
        result.append(c)
print(''.join(result))