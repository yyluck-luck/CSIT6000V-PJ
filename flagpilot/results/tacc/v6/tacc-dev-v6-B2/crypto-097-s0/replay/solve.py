with open('challenge/encrypted.txt') as f:
    s = f.read().strip()

# Try all Caesar shifts to find the one that produces picoCTF
for shift in range(1, 26):
    result = ''
    for c in s:
        if c.isalpha():
            base = ord('A') if c.isupper() else ord('a')
            result += chr((ord(c) - base + shift) % 26 + base)
        else:
            result += c
    if 'picoCTF' in result:
        print(result)
        break