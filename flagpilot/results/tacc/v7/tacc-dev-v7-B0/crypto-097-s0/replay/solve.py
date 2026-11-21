with open('./challenge/encrypted.txt', 'r') as f:
    encrypted = f.read().strip()

# Try all Caesar shifts to find the one that produces picoCTF
for shift in range(26):
    result = ""
    for c in encrypted:
        if c.isalpha():
            if c.isupper():
                result += chr((ord(c) - ord('A') - shift) % 26 + ord('A'))
            else:
                result += chr((ord(c) - ord('a') - shift) % 26 + ord('a'))
        else:
            result += c
    if "picoCTF" in result:
        print(result)
        break