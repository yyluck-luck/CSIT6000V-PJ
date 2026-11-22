import os
os.chdir('./challenge')
codebook = open('codebook.txt').read()
password = codebook[4]+codebook[14]+codebook[13]+codebook[14]+codebook[23]+codebook[25]+codebook[16]+codebook[0]+codebook[25]
flag_enc = chr(0x13)+chr(0x01)+chr(0x17)+chr(0x07)+chr(0x2c)+chr(0x3a)+chr(0x2f)+chr(0x1a)+chr(0x0d)+chr(0x53)+chr(0x0c)+chr(0x47)+chr(0x0a)+chr(0x5f)+chr(0x5e)+chr(0x02)+chr(0x3e)+chr(0x5a)+chr(0x56)+chr(0x5d)+chr(0x45)+chr(0x5d)+chr(0x58)+chr(0x31)+chr(0x0d)+chr(0x58)+chr(0x0f)+chr(0x02)+chr(0x5a)+chr(0x10)+chr(0x0e)+chr(0x5d)+chr(0x13)
new_key = password
i=0
while len(new_key)<len(flag_enc):
    new_key = new_key + password[i]
    i=(i+1)%len(password)
flag = ''.join(chr(ord(a)^ord(b)) for a,b in zip(flag_enc,new_key))
print(flag)
