text = open('./challenge/encrypted.txt').read().strip()
result = ""
for c in text:
    if c.isalpha():
        if c.isupper():
            result += chr((ord(c) - ord('A') - 8) % 26 + ord('A'))
        else:
            result += chr((ord(c) - ord('a') - 8) % 26 + ord('a'))
    else:
        result += c
print(result)