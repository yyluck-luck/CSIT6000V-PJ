import string

ALPHABET = string.ascii_lowercase[:16]

def b16_decode(enc):
    plain = ""
    for i in range(0, len(enc), 2):
        c1 = ALPHABET.index(enc[i])
        c2 = ALPHABET.index(enc[i+1])
        byte = (c1 << 4) | c2
        plain += chr(byte)
    return plain

def unshift(c, k):
    t1 = ALPHABET.index(c)
    t2 = ALPHABET.index(k)
    return ALPHABET[(t1 - t2) % 16]

enc = "dcebcmebecamcmanaedbacdaanafagapdaaoabaaafdbapdpaaapadanandcafaadbdaapdpandcac"
key = "n"

b16 = ""
for c in enc:
    b16 += unshift(c, key)
plain = b16_decode(b16)
print(f"picoCTF{{{plain}}}")