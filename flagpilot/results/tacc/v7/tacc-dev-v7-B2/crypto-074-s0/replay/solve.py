def vigenere_decrypt(ciphertext, key):
    plaintext = []
    for i, c in enumerate(ciphertext):
        if c.isalpha():
            k = key[i % len(key)]
            p = (ord(c.upper()) - ord('A') - (ord(k.upper()) - ord('A'))) % 26
            plaintext.append(chr(p + ord('A')))
        else:
            plaintext.append(c)
    return ''.join(plaintext)

ciphertext = "UFJKXQZQUNB"
key = "SOLVECRYPTO"
result = vigenere_decrypt(ciphertext, key)
print(f"picoCTF{{{result}}}")