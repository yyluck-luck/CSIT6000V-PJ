with open('./challenge/encrypted.txt', 'r') as f:
    ciphertext = f.read().strip()

def caesar_decrypt(text, shift):
    result = []
    for c in text:
        if c.isalpha():
            base = ord('A') if c.isupper() else ord('a')
            result.append(chr((ord(c) - base + shift) % 26 + base))
        else:
            result.append(c)
    return ''.join(result)

# Try all shifts to find the one that produces picoCTF{ prefix
for shift in range(1, 26):
    decrypted = caesar_decrypt(ciphertext, -shift)
    if decrypted.startswith('picoCTF{'):
        print(decrypted)
        break
    decrypted = caesar_decrypt(ciphertext, shift)
    if decrypted.startswith('picoCTF{'):
        print(decrypted)
        break