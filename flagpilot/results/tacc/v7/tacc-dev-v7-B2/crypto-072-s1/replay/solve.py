import string

LOWERCASE_OFFSET = ord("a")
ALPHABET = string.ascii_lowercase[:16]

def shift(c, k):
    t1 = ord(c) - LOWERCASE_OFFSET
    t2 = ord(k) - LOWERCASE_OFFSET
    return ALPHABET[(t1 + t2) % len(ALPHABET)]

enc = "dcebcmebecamcmanaedbacdaanafagapdaaoabaaafdbapdpaaapadanandcafaadbdaapdpandcac"
key = "n"

dec_b16 = ""
for c in enc:
    t1 = ord(c) - LOWERCASE_OFFSET
    t2 = ord(key) - LOWERCASE_OFFSET
    dec_b16 += ALPHABET[(t1 - t2) % len(ALPHABET)]

plain = ""
for i in range(0, len(dec_b16), 2):
    hi = ALPHABET.index(dec_b16[i])
    lo = ALPHABET.index(dec_b16[i+1])
    byte = (hi << 4) | lo
    plain += chr(byte)

print(f"picoCTF{{{plain}}}")