def vigenere_decrypt(ciphertext, key, table):
    # Parse the table to build a lookup
    # table is a list of rows, each row is a list of 26 chars
    # table[row][col] = result of (row + col) mod 26
    # For decryption: plaintext = (ciphertext - key) mod 26
    # We can use the table: find the row for key, find the column where table[key_row][col] == ciphertext
    result = []
    key = key.upper()
    for i, c in enumerate(ciphertext):
        if c.isalpha():
            k = key[i % len(key)]
            k_idx = ord(k) - ord('A')
            c_idx = ord(c.upper()) - ord('A')
            # Find plaintext p such that table[k_idx][p] == c
            # table[k_idx][p] = (k_idx + p) % 26 = c_idx
            # p = (c_idx - k_idx) % 26
            p = (c_idx - k_idx) % 26
            # Verify using the table
            assert table[k_idx][p] == c.upper(), f"Table mismatch at position {i}"
            result.append(chr(p + ord('A')))
        else:
            result.append(c)
    return ''.join(result)

# Read the table from the challenge file
with open('./challenge/table.txt', 'r') as f:
    lines = f.readlines()

table = []
for line in lines:
    line = line.strip()
    if line.startswith('A |') or line.startswith('B |') or line.startswith('C |') or line.startswith('D |') or line.startswith('E |') or line.startswith('F |') or line.startswith('G |') or line.startswith('H |') or line.startswith('I |') or line.startswith('J |') or line.startswith('K |') or line.startswith('L |') or line.startswith('M |') or line.startswith('N |') or line.startswith('O |') or line.startswith('P |') or line.startswith('Q |') or line.startswith('R |') or line.startswith('S |') or line.startswith('T |') or line.startswith('U |') or line.startswith('V |') or line.startswith('W |') or line.startswith('X |') or line.startswith('Y |') or line.startswith('Z |'):
        row = line.split('|')[1].strip().split()
        table.append(row)

assert len(table) == 26, f"Expected 26 rows, got {len(table)}"
for row in table:
    assert len(row) == 26, f"Expected 26 cols, got {len(row)}"

# The ciphertext and key from the challenge description
ciphertext = "UFJKXQZQUNB"
key = "SOLVECRYPTO"

plaintext = vigenere_decrypt(ciphertext, key, table)
print(f"picoCTF{{{plaintext}}}")