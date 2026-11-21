import codecs

# Read the challenge file to confirm it's a ROT13 implementation
with open('./challenge/rot13.py', 'r') as f:
    content = f.read()

# The file contains a ROT13 cipher implementation
# Apply ROT13 to the encoded flag from the challenge
encoded = 'cvpbPGS{abg_gbb_onq_bs_n_ceboyrz}'
flag = codecs.decode(encoded, 'rot13')
print(flag)