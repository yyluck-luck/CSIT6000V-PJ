import string

ALPHABET = string.ascii_lowercase[:16]
LOWERCASE_OFFSET = ord("a")

enc = "dcebcmebecamcmanaedbacdaanafagapdaaoabaaafdbapdpaaapadanandcafaadbdaapdpandcac"

def unshift(c, k):
    t1 = ord(c) - LOWERCASE_OFFSET
    t2 = ord(k) - LOWERCASE_OFFSET
    return ALPHABET[(t1 - t2) % len(ALPHABET)]

def b16_decode(enc):
    plain = ""
    for i in range(0, len(enc), 2):
        c1 = enc[i]
        c2 = enc[i+1]
        b1 = ALPHABET.index(c1)
        b2 = ALPHABET.index(c2)
        byte_val = (b1 << 4) | b2
        plain += chr(byte_val)
    return plain

key = 'n'
b16 = ""
for i, c in enumerate(enc):
    b16 += unshift(c, key)
plain = b16_decode(b16)
print(f"picoCTF{{{plain}}}")