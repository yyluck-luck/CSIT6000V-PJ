import sys

# The encrypted flag from the challenge description
flag = 'cvpbPGS{abg_gbb_onq_bs_n_ceboyrz}'

AZ = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
az = "abcdefghijklmnopqrstuvwxyz"
s = ""

for x in flag:
    if x in AZ:
        s += AZ[(AZ.index(x)+13)%len(AZ)]
    elif x in az:
        s += az[(az.index(x)+13)%len(az)]
    else:
        s += x

print(s)