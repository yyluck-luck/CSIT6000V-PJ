import string

LOWERCASE_OFFSET = ord("a")
ALPHABET = string.ascii_lowercase[:16]

ciphertext = "dcebcmebecamcmanaedbacdaanafagapdaaoabaaafdbapdpaaapadanandcafaadbdaapdpandcac"

def b16_decode(enc):
    plain = ""
    for i in range(0, len(enc), 2):
        c1 = ALPHABET.index(enc[i])
        c2 = ALPHABET.index(enc[i+1])
        binary = format(c1, '04b') + format(c2, '04b')
        plain += chr(int(binary, 2))
    return plain

def unshift(c, k):
    t1 = ord(c) - LOWERCASE_OFFSET
    t2 = ord(k) - LOWERCASE_OFFSET
    return ALPHABET[(t1 - t2) % len(ALPHABET)]

key_char = 'n'
dec = ""
for i, c in enumerate(ciphertext):
    dec += unshift(c, key_char)
plain = b16_decode(dec)
print(f"picoCTF{{{plain}}}")