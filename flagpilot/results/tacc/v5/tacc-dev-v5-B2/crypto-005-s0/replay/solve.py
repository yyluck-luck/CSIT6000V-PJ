import codecs

# Read the challenge file to confirm it's a ROT13 implementation
with open('challenge/rot13.py', 'r') as f:
    content = f.read()

# The challenge provides the ROT13-encoded flag in the prompt
# The file confirms ROT13 is the cipher to use
encoded = 'cvpbPGS{abg_gbb_onq_bs_n_ceboyrz}'

# Apply ROT13 decode (same as encode for ROT13)
flag = codecs.decode(encoded, 'rot13')
print(flag)