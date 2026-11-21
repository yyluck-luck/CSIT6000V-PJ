with open('challenge/encrypted.txt') as f:
    s = f.read().strip()
result = ""
for c in s:
    if c.isalpha():
        if c.isupper():
            result += chr((ord(c) - ord('A') - 8) % 26 + ord('A'))
        else:
            result += chr((ord(c) - ord('a') - 8) % 26 + ord('a'))
    else:
        result += c
print(result)