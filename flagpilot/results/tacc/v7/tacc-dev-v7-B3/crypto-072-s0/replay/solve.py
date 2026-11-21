import string
import re

# Read the algorithm from the challenge file
with open('./challenge/new_caesar.py', 'r') as f:
    src = f.read()

# Extract ALPHABET from the source
m = re.search(r'ALPHABET\s*=\s*string\.ascii_lowercase\[:\d+\]', src)
if m:
    # Parse the slice value
    val = int(m.group(0).split(':')[1].rstrip(']'))
    ALPHABET = string.ascii_lowercase[:val]
else:
    ALPHABET = string.ascii_lowercase[:16]

LOWERCASE_OFFSET = ord("a")

# The encrypted text from the challenge description
enc = "dcebcmebecamcmanaedbacdaanafagapdaaoabaaafdbapdpaaapadanandcafaadbdaapdpandcac"

def b16_decode(enc):
    plain = ""
    for i in range(0, len(enc), 2):
        c1 = ALPHABET.index(enc[i])
        c2 = ALPHABET.index(enc[i+1])
        byte = (c1 << 4) | c2
        plain += chr(byte)
    return plain

def unshift(c, k):
    t1 = ord(c) - LOWERCASE_OFFSET
    t2 = ord(k) - LOWERCASE_OFFSET
    return ALPHABET[(t1 - t2) % len(ALPHABET)]

for k in ALPHABET:
    dec = ""
    for c in enc:
        dec += unshift(c, k)
    try:
        plain = b16_decode(dec)
        if all(32 <= ord(ch) < 127 for ch in plain):
            print(f"picoCTF{{{plain}}}")
            break
    except:
        pass