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

enc = "dcebcmebecamcmanaedbacdaanafagapdaaoabaaafdbapdpaaapadanandcafaadbdaapdpandcac"

for key in ALPHABET:
    dec = ""
    for i, c in enumerate(enc):
        t1 = ALPHABET.index(c)
        t2 = ALPHABET.index(key)
        dec += ALPHABET[(t1 - t2) % 16]
    plain = b16_decode(dec)
    if all(32 <= ord(ch) < 127 for ch in plain):
        print(f"picoCTF{{{plain}}}")
        break