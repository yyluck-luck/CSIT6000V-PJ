import os
os.chdir('./challenge')
src = open('code.py').read()
ns = {}
exec(src, ns)
codebook = open('codebook.txt').read()
password = codebook[4] + codebook[14] + codebook[13] + codebook[14] + codebook[23]+ codebook[25] + codebook[16] + codebook[0] + codebook[25]
print(ns['str_xor'](ns['flag_enc'], password))