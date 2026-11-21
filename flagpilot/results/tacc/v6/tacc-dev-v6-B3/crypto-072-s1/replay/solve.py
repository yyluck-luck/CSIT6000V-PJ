import string
ALPHABET = string.ascii_lowercase[:16]
OFF = ord('a')
enc = 'dcebcmebecamcmanaedbacdaanafagapdaaoabaaafdbapdpaaapadanandcafaadbdaapdpandcac'
for k in ALPHABET:
    t2 = ord(k) - OFF
    b16 = ''.join(ALPHABET[(ord(c) - OFF - t2) % 16] for c in enc)
    out = bytes((ALPHABET.index(b16[i]) << 4) | ALPHABET.index(b16[i+1]) for i in range(0, len(b16), 2))
    s = out.decode('latin-1')
    if all(32 <= ord(ch) < 127 for ch in s):
        print('picoCTF{' + s + '}')
        break