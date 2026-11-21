import sys
sys.path.insert(0, './challenge')
import code

with open('./challenge/codebook.txt') as f:
    codebook = f.read()

password = (codebook[4] + codebook[14] + codebook[13] + codebook[14] +
            codebook[23] + codebook[25] + codebook[16] + codebook[0] +
            codebook[25])

flag = code.str_xor(code.flag_enc, password)
print(flag)
