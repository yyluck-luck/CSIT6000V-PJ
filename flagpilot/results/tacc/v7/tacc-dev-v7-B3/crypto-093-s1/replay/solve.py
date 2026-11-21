from itertools import permutations
s = open('./challenge/message.txt').read().rstrip('\n')
blocks = [s[i:i+3] for i in range(0, len(s), 3)]
for p in permutations(range(3)):
    first = ''.join(blocks[0][i] for i in p)
    if first == 'The':
        result = []
        for b in blocks:
            result.append(''.join(b[i] for i in p))
        msg = ''.join(result)
        start = msg.index('picoCTF{')
        end = msg.index('}', start) + 1
        print(msg[start:end])
        break