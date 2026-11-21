import subprocess, sys

# The challenge description provides the ROT13-encoded flag:
# cvpbPGS{abg_gbb_onq_bs_n_ceboyrz}
# We apply ROT13 to decode it using the same logic as challenge/rot13.py

AZ = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
az = "abcdefghijklmnopqrstuvwxyz"

encoded = "cvpbPGS{abg_gbb_onq_bs_n_ceboyrz}"
s = ""
for x in encoded:
    if x in AZ:
        s += AZ[(AZ.index(x)+13) % len(AZ)]
    elif x in az:
        s += az[(az.index(x)+13) % len(az)]
    else:
        s += x

print(s)