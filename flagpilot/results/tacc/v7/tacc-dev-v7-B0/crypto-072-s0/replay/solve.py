import string

ALPHABET = string.ascii_lowercase[:16]
LOWERCASE_OFFSET = ord("a")

def b16_decode(enc):
    plain = b""
    for i in range(0, len(enc), 2):
        c1 = enc[i]
        c2 = enc[i+1]
        v1 = ALPHABET.index(c1)
        v2 = ALPHABET.index(c2)
        byte = (v1 << 4) | v2
        plain += bytes([byte])
    return plain

def unshift(c, k):
    t1 = ord(c) - LOWERCASE_OFFSET
    t2 = ord(k) - LOWERCASE_OFFSET
    return ALPHABET[(t1 - t2) % len(ALPHABET)]

enc = "dcebcmebecamcmanaedbacdaanafagapdaaoabaaafdbapdpaaapadanandcafaadbdaapdpandcac"
key = "n"
b16 = ""
for c in enc:
    b16 += unshift(c, key)
plain = b16_decode(b16).decode('utf-8')
print(f"picoCTF{{{plain}}}")
