import re

# Read the challenge script to verify it implements ROT13
with open('./challenge/rot13.py', 'r') as f:
    content = f.read()

# Verify the script contains ROT13 logic (shift by 13)
assert '13' in content, "Script does not appear to implement ROT13"

# The encrypted text from the challenge description
encrypted = 'cvpbPGS{abg_gbb_onq_bs_n_ceboyrz}'

# Apply ROT13 using the same logic as the challenge script
AZ = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
az = "abcdefghijklmnopqrstuvwxyz"
s = ""
for x in encrypted:
    if x in AZ:
        s += AZ[(AZ.index(x)+13)%len(AZ)]
    elif x in az:
        s += az[(az.index(x)+13)%len(az)]
    else:
        s += x

print(s)