import re

# Read the table from challenge file
with open('./challenge/table.txt', 'r') as f:
    content = f.read()

# Parse the table to build the Vigenere lookup
rows = {}
for line in content.split('\n'):
    line = line.strip()
    if '|' in line:
        parts = line.split('|')
        row_label = parts[0].strip()
        if len(row_label) == 1 and row_label.isalpha() and row_label.isupper():
            cells = parts[1].split()
            rows[row_label] = cells

assert len(rows) == 26, f"Expected 26 rows, got {len(rows)}"

ciphertext = "UFJKXQZQUNB"
key = "SOLVECRYPTO"

plaintext = ""
for i, c in enumerate(ciphertext):
    k = key[i % len(key)]
    row = rows[k]
    idx = row.index(c)
    p = chr(ord('A') + idx)
    plaintext += p

print(f"picoCTF{{{plaintext}}}")