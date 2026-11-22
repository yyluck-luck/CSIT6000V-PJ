ciphertext = "UFJKXQZQUNB"
key = "SOLVECRYPTO"
plaintext = ""
for i, c in enumerate(ciphertext):
    k = key[i % len(key)]
    p = (ord(c) - ord('A') - (ord(k) - ord('A'))) % 26
    plaintext += chr(p + ord('A'))
print(f"picoCTF{{{plaintext}}}")