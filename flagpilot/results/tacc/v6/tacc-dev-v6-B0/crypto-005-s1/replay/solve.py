import os

# Read the encrypted string from the challenge material
with open('./challenge/encrypted.txt', 'r') as f:
    encrypted = f.read().strip()

# Read rot13.py to confirm it's a ROT13 cipher
with open('./challenge/rot13.py', 'r') as f:
    source = f.read()

# Apply ROT13 decode (same as encode since ROT13 is its own inverse)
AZ = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
az = "abcdefghijklmnopqrstuvwxyz"
s = ""
for x in encrypted:
    if x in AZ:
        s += AZ[(AZ.index(x) + 13) % len(AZ)]
    elif x in az:
        s += az[(az.index(x) + 13) % len(az)]
    else:
        s += x

print(s)