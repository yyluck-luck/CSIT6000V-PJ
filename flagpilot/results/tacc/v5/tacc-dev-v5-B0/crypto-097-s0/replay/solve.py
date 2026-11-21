with open('./challenge/encrypted.txt', 'r') as f:
    data = f.read().strip()

result = []
for c in data:
    if c.isalpha():
        if c.islower():
            result.append(chr((ord(c) - ord('a') - 8) % 26 + ord('a')))
        else:
            result.append(chr((ord(c) - ord('A') - 8) % 26 + ord('A')))
    else:
        result.append(c)

print(''.join(result))