with open('./challenge/encrypted.txt', 'r') as f:
    ciphertext = f.read().strip()

shift = 8
result = ""
for c in ciphertext:
    if c.isalpha():
        if c.isupper():
            result += chr((ord(c) - ord('A') - shift) % 26 + ord('A'))
        else:
            result += chr((ord(c) - ord('a') - shift) % 26 + ord('a'))
    else:
        result += c
print(result)