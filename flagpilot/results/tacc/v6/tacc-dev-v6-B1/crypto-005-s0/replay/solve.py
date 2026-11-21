import re

# Read the challenge file to extract the encrypted flag
with open('./challenge/rot13.py', 'r') as f:
    content = f.read()

# The encrypted flag is given in the challenge description, but let's also check if it's in the file
# The challenge says: cvpbPGS{abg_gbb_onq_bs_n_ceboyrz}
# Let's extract it from the challenge text or use the known input
# Since the file only has the ROT13 function, the input is from the challenge description
# We need to read it from somewhere - let's check if there's a separate input

# The encrypted flag is provided in the challenge statement
# Let's look for it in the file or use the standard approach
# Actually, the challenge provides the encrypted string in the problem description
# We'll use the ROT13 function from the file to decode it

# Extract the ROT13 logic from the file and apply it to the known encrypted string
# The encrypted string is: cvpbPGS{abg_gbb_onq_bs_n_ceboyrz}

AZ = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
az = "abcdefghijklmnopqrstuvwxyz"

encrypted = "cvpbPGS{abg_gbb_onq_bs_n_ceboyrz}"
s = ""
for x in encrypted:
    if x in AZ:
        s += AZ[(AZ.index(x)+13)%len(AZ)]
    elif x in az:
        s += az[(az.index(x)+13)%len(az)]
    else:
        s += x

print(s)