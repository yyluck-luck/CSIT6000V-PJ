ciphertext = "UFJKXQZQUNB"
key = "SOLVECRYPTO"
key = key[:len(ciphertext)]
plaintext = ""
for c, k in zip(ciphertext, key):
    c_idx = ord(c) - ord('A')
    k_idx = ord(k) - ord('A')
    p_idx = (c_idx - k_idx) % 26
    plaintext += chr(p_idx + ord('A'))
print(f"picoCTF{{{plaintext}}}")