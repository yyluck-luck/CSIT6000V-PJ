import re

with open('./challenge/message.txt', 'r') as f:
    msg = f.read().strip()

blocks = [msg[i:i+3] for i in range(0, len(msg), 3)]

# The permutation (1,2,0) means: scrambled[0]=orig[1], scrambled[1]=orig[2], scrambled[2]=orig[0]
# So orig[1]=scrambled[0], orig[2]=scrambled[1], orig[0]=scrambled[2]
perm = (1, 2, 0)
recovered = []
for block in blocks:
    orig = ['']*3
    for i in range(3):
        orig[perm[i]] = block[i]
    recovered.append(''.join(orig))
result = ''.join(recovered)

flag = re.search(r'picoCTF\{[^}]+\}', result).group()
print(flag)