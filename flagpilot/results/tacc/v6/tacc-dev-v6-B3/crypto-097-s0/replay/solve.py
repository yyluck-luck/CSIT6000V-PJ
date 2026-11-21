s=open('challenge/encrypted.txt').read().strip()
def dec(c):
    if c.isupper():
        return chr((ord(c)-ord('A')-8)%26+ord('A'))
    if c.islower():
        return chr((ord(c)-ord('a')-8)%26+ord('a'))
    return c
print(''.join(dec(c) for c in s))