import codecs

# Read the challenge file to confirm it's a ROT13 implementation
with open('./challenge/rot13.py', 'r') as f:
    content = f.read()

# Extract the shift value from the file (it uses +13)
shift = 13
assert '+13' in content, "File should contain ROT13 shift"

# The ciphertext from the challenge description
ciphertext = 'cvpbPGS{abg_gbb_onq_bs_n_ceboyrz}'

# Apply ROT13 using the same logic as the file
AZ = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
az = "abcdefghijklmnopqrstuvwxyz"
s = ""
for x in ciphertext:
    if x in AZ:
        s += AZ[(AZ.index(x) + shift) % len(AZ)]
    elif x in az:
        s += az[(az.index(x) + shift) % len(az)]
    else:
        s += x

print(s)