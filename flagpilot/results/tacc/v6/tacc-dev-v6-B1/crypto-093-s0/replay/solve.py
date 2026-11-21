import re

with open('./challenge/message.txt', 'r') as f:
    msg = f.read().strip()

# The corruption: every block of 3 characters was rotated by 2 (shifted right by 2)
# To reverse: rotate each block of 3 left by 2 (or right by 1)
block_size = 3
shift = 2
blocks = [msg[i:i+block_size] for i in range(0, len(msg), block_size)]
shifted_blocks = [b[shift:] + b[:shift] for b in blocks]
result = ''.join(shifted_blocks)

flag = re.search(r'picoCTF\{[^}]+\}', result).group()
print(flag)