with open('challenge/message.txt') as f:
    numbers = [int(x) for x in f.read().split()]

charset = {}
for i in range(1, 27):
    charset[i] = chr(ord('A') + i - 1)
for i in range(27, 37):
    charset[i] = str(i - 27)
charset[37] = '_'

result = []
for n in numbers:
    r = n % 41
    inv = pow(r, -1, 41)
    result.append(charset[inv])

msg = ''.join(result)
print('picoCTF{' + msg + '}')