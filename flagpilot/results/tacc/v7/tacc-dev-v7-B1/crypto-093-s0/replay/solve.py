import re

with open('./challenge/message.txt', 'r') as f:
    s = f.read().strip()

# Each block of 3 characters was scrambled with permutation [2,0,1]
# To unscramble: new[0]=old[2], new[1]=old[0], new[2]=old[1]
result = []
for i in range(0, len(s), 3):
    block = s[i:i+3]
    if len(block) == 3:
        result.append(block[2] + block[0] + block[1])
    else:
        result.append(block)
msg = ''.join(result)

m = re.search(r'picoCTF\{[^}]+\}', msg)
print(m.group(0))